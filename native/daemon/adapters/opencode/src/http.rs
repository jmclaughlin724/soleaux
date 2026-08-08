//! Minimal HTTP/1.1 transport for a loopback `opencode serve` instance.
//!
//! Scope is deliberately narrow: `http://` to a loopback host only, JSON
//! request/response bodies, and `text/event-stream` reads. Supporting exactly
//! the framings a local server can answer with (content-length, chunked,
//! connection-close) keeps the workspace dependency tree unchanged; anything
//! outside that subset fails with a typed error instead of being guessed at.

use anyhow::{Context, Result, bail};
use serde_json::Value;
use std::net::IpAddr;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use url::Url;

pub(crate) const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const RESPONSE_MAX_BYTES: usize = 16 * 1024 * 1024;
const HEAD_MAX_BYTES: usize = 64 * 1024;
const SSE_LINE_MAX_BYTES: usize = 1024 * 1024;
const SSE_DATA_MAX_BYTES: usize = 4 * 1024 * 1024;
const READ_BUFFER_BYTES: usize = 16 * 1024;

/// A parsed response head: status code plus lowercase header names.
#[derive(Debug)]
pub(crate) struct ResponseHead {
    pub(crate) status: u16,
    pub(crate) headers: Vec<(String, String)>,
}

impl ResponseHead {
    pub(crate) fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(candidate, _)| candidate == name)
            .map(|(_, value)| value.as_str())
    }
}

#[derive(Debug)]
pub(crate) enum BodyFraming {
    Length(usize),
    Chunked,
    ReadToEof,
}

/// Resolve the loopback authority of a base URL, refusing anything else.
/// The adapter drives a local development server; a non-loopback host would
/// silently extend the mutation surface across the network.
pub(crate) fn loopback_authority(base: &Url) -> Result<(String, u16)> {
    if base.scheme() != "http" {
        bail!(
            "opencode adapter supports only http:// loopback URLs, got scheme {}",
            base.scheme()
        );
    }
    let host = base
        .host_str()
        .context("opencode base URL has no host")?
        .to_string();
    let loopback = match host.as_str() {
        "localhost" => true,
        literal => literal
            .trim_start_matches('[')
            .trim_end_matches(']')
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false),
    };
    if !loopback {
        bail!("opencode adapter refuses non-loopback host {host}");
    }
    let port = base.port_or_known_default().unwrap_or(80);
    Ok((host, port))
}

/// Reject path segments that would change the request target grammar. Every
/// OpenCode identifier this adapter interpolates (`ses…`, `msg…`, `per…`)
/// stays inside this alphabet.
pub(crate) fn validate_path_segment(segment: &str) -> Result<()> {
    if segment.is_empty() {
        bail!("path segment is empty");
    }
    if segment == "." || segment == ".." {
        bail!("path segment {segment:?} is a relative traversal");
    }
    if !segment
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        bail!("path segment {segment:?} contains characters outside [A-Za-z0-9._-]");
    }
    Ok(())
}

/// Parse a response head from `buffer`. Returns the head and the number of
/// bytes it consumed, or `None` when the terminator has not arrived yet.
pub(crate) fn parse_response_head(buffer: &[u8]) -> Result<Option<(ResponseHead, usize)>> {
    let Some(end) = find_head_end(buffer) else {
        if buffer.len() > HEAD_MAX_BYTES {
            bail!("response head exceeds {HEAD_MAX_BYTES} bytes");
        }
        return Ok(None);
    };
    let head = std::str::from_utf8(&buffer[..end]).context("response head is not UTF-8")?;
    let mut lines = head.split("\r\n");
    let status_line = lines.next().context("response head is empty")?;
    let mut parts = status_line.splitn(3, ' ');
    let version = parts.next().unwrap_or_default();
    if version != "HTTP/1.1" && version != "HTTP/1.0" {
        bail!("unsupported HTTP version {version:?}");
    }
    let status: u16 = parts
        .next()
        .context("status line has no status code")?
        .parse()
        .context("status code is not numeric")?;
    let mut headers = Vec::new();
    for line in lines {
        if line.is_empty() {
            continue;
        }
        if line.starts_with(' ') || line.starts_with('\t') {
            bail!("obsolete header line folding is not supported");
        }
        let (name, value) = line
            .split_once(':')
            .with_context(|| format!("header line without a colon: {line:?}"))?;
        headers.push((name.trim().to_ascii_lowercase(), value.trim().to_string()));
    }
    Ok(Some((ResponseHead { status, headers }, end + 4)))
}

fn find_head_end(buffer: &[u8]) -> Option<usize> {
    buffer.windows(4).position(|window| window == b"\r\n\r\n")
}

/// Choose the body framing mandated by the head. Conflicting or unsupported
/// framing declarations fail instead of guessing.
pub(crate) fn body_framing(head: &ResponseHead) -> Result<BodyFraming> {
    if let Some(encoding) = head.header("transfer-encoding") {
        let codings: Vec<&str> = encoding.split(',').map(str::trim).collect();
        if codings
            .last()
            .map(|last| last.eq_ignore_ascii_case("chunked"))
            == Some(true)
            && codings.len() == 1
        {
            return Ok(BodyFraming::Chunked);
        }
        bail!("unsupported transfer-encoding {encoding:?}");
    }
    let lengths: Vec<&str> = head
        .headers
        .iter()
        .filter(|(name, _)| name == "content-length")
        .map(|(_, value)| value.as_str())
        .collect();
    match lengths.as_slice() {
        [] => Ok(BodyFraming::ReadToEof),
        [single] => {
            let length: usize = single.parse().context("content-length is not numeric")?;
            Ok(BodyFraming::Length(length))
        }
        _ => bail!("multiple content-length headers"),
    }
}

#[derive(Debug, PartialEq, Eq)]
enum ChunkState {
    Size,
    Data(usize),
    DataCr,
    DataLf,
    Trailer,
    Done,
}

/// Incremental `Transfer-Encoding: chunked` decoder. `feed` consumes any
/// split of the wire bytes and appends decoded payload bytes to `out`.
#[derive(Debug)]
pub(crate) struct ChunkDecoder {
    state: ChunkState,
    line: Vec<u8>,
}

impl ChunkDecoder {
    pub(crate) fn new() -> Self {
        Self {
            state: ChunkState::Size,
            line: Vec::new(),
        }
    }

    pub(crate) fn is_done(&self) -> bool {
        self.state == ChunkState::Done
    }

    pub(crate) fn feed(&mut self, input: &[u8], out: &mut Vec<u8>) -> Result<()> {
        let mut position = 0;
        while position < input.len() {
            match self.state {
                ChunkState::Size => {
                    position += self.take_line(&input[position..])?;
                    if self.line_complete() {
                        let line = self.finish_line()?;
                        let size_text = line.split(';').next().unwrap_or_default().trim();
                        let size = usize::from_str_radix(size_text, 16)
                            .with_context(|| format!("chunk size {size_text:?} is not hex"))?;
                        self.state = if size == 0 {
                            ChunkState::Trailer
                        } else {
                            ChunkState::Data(size)
                        };
                    }
                }
                ChunkState::Data(remaining) => {
                    let take = remaining.min(input.len() - position);
                    out.extend_from_slice(&input[position..position + take]);
                    position += take;
                    if take == remaining {
                        self.state = ChunkState::DataCr;
                    } else {
                        self.state = ChunkState::Data(remaining - take);
                    }
                }
                ChunkState::DataCr => {
                    if input[position] != b'\r' {
                        bail!("chunk data is not terminated by CRLF");
                    }
                    position += 1;
                    self.state = ChunkState::DataLf;
                }
                ChunkState::DataLf => {
                    if input[position] != b'\n' {
                        bail!("chunk data is not terminated by CRLF");
                    }
                    position += 1;
                    self.state = ChunkState::Size;
                }
                ChunkState::Trailer => {
                    position += self.take_line(&input[position..])?;
                    if self.line_complete() {
                        let line = self.finish_line()?;
                        if line.is_empty() {
                            self.state = ChunkState::Done;
                        }
                    }
                }
                ChunkState::Done => break,
            }
        }
        Ok(())
    }

    fn take_line(&mut self, input: &[u8]) -> Result<usize> {
        for (offset, byte) in input.iter().enumerate() {
            self.line.push(*byte);
            if self.line.len() > HEAD_MAX_BYTES {
                bail!("chunk metadata line exceeds {HEAD_MAX_BYTES} bytes");
            }
            if *byte == b'\n' {
                return Ok(offset + 1);
            }
        }
        Ok(input.len())
    }

    fn line_complete(&self) -> bool {
        self.line.last() == Some(&b'\n')
    }

    fn finish_line(&mut self) -> Result<String> {
        let mut line = std::mem::take(&mut self.line);
        line.pop();
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        String::from_utf8(line).context("chunk metadata line is not UTF-8")
    }
}

/// One dispatched server-sent event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SseFrame {
    pub event: Option<String>,
    pub data: String,
    pub id: Option<String>,
}

/// Incremental SSE field parser: feed complete lines, receive dispatched
/// frames on blank lines per the WHATWG event-stream grammar.
#[derive(Debug, Default)]
pub(crate) struct SseParser {
    data: Vec<String>,
    event: Option<String>,
    id: Option<String>,
}

impl SseParser {
    pub(crate) fn feed_line(&mut self, line: &str) -> Result<Option<SseFrame>> {
        if line.is_empty() {
            if self.data.is_empty() {
                self.event = None;
                return Ok(None);
            }
            let frame = SseFrame {
                event: self.event.take(),
                data: self.data.join("\n"),
                id: self.id.clone(),
            };
            self.data.clear();
            return Ok(Some(frame));
        }
        if line.starts_with(':') {
            return Ok(None);
        }
        let (field, value) = match line.split_once(':') {
            Some((field, value)) => (field, value.strip_prefix(' ').unwrap_or(value)),
            None => (line, ""),
        };
        match field {
            "data" => {
                self.data.push(value.to_string());
                let total: usize = self.data.iter().map(String::len).sum();
                if total > SSE_DATA_MAX_BYTES {
                    bail!("server-sent event data exceeds {SSE_DATA_MAX_BYTES} bytes");
                }
            }
            "event" => self.event = Some(value.to_string()),
            "id" if !value.contains('\0') => self.id = Some(value.to_string()),
            _ => {}
        }
        Ok(None)
    }
}

fn write_request_head(
    method: &str,
    path_and_query: &str,
    host: &str,
    port: u16,
    accept: &str,
    body: Option<&[u8]>,
    close: bool,
) -> Vec<u8> {
    let mut head = format!(
        "{method} {path_and_query} HTTP/1.1\r\nhost: {host}:{port}\r\naccept: {accept}\r\nuser-agent: soleaux-adapter-opencode/{}\r\n",
        env!("CARGO_PKG_VERSION"),
    );
    if close {
        head.push_str("connection: close\r\n");
    }
    if let Some(body) = body {
        head.push_str("content-type: application/json\r\n");
        head.push_str(&format!("content-length: {}\r\n", body.len()));
    }
    head.push_str("\r\n");
    let mut bytes = head.into_bytes();
    if let Some(body) = body {
        bytes.extend_from_slice(body);
    }
    bytes
}

async fn connect(base: &Url) -> Result<TcpStream> {
    let (host, port) = loopback_authority(base)?;
    let stream = tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect((host.as_str(), port)))
        .await
        .with_context(|| format!("connecting to {host}:{port} timed out"))?
        .with_context(|| format!("connecting to {host}:{port}"))?;
    Ok(stream)
}

/// Send one JSON request and read the complete response body.
pub(crate) async fn request(
    base: &Url,
    timeout: Duration,
    method: &str,
    path_and_query: &str,
    body: Option<&Value>,
) -> Result<(u16, Vec<u8>)> {
    let outcome = tokio::time::timeout(timeout, request_inner(base, method, path_and_query, body))
        .await
        .with_context(|| format!("{method} {path_and_query} timed out"))?;
    outcome.with_context(|| format!("{method} {path_and_query}"))
}

async fn request_inner(
    base: &Url,
    method: &str,
    path_and_query: &str,
    body: Option<&Value>,
) -> Result<(u16, Vec<u8>)> {
    let (host, port) = loopback_authority(base)?;
    let mut stream = connect(base).await?;
    let encoded_body = body.map(serde_json::to_vec).transpose()?;
    let request_bytes = write_request_head(
        method,
        path_and_query,
        &host,
        port,
        "application/json",
        encoded_body.as_deref(),
        true,
    );
    stream.write_all(&request_bytes).await?;

    let mut raw = Vec::new();
    let mut chunk = vec![0_u8; READ_BUFFER_BYTES];
    let (head, consumed) = loop {
        if let Some(parsed) = parse_response_head(&raw)? {
            break parsed;
        }
        let read = stream.read(&mut chunk).await?;
        if read == 0 {
            bail!("connection closed before a complete response head");
        }
        raw.extend_from_slice(&chunk[..read]);
        if raw.len() > RESPONSE_MAX_BYTES {
            bail!("response exceeds {RESPONSE_MAX_BYTES} bytes");
        }
    };

    let framing = body_framing(&head)?;
    let mut body_bytes = Vec::new();
    match framing {
        BodyFraming::Length(length) => {
            body_bytes.extend_from_slice(&raw[consumed..raw.len().min(consumed + length)]);
            while body_bytes.len() < length {
                let read = stream.read(&mut chunk).await?;
                if read == 0 {
                    bail!("connection closed before {length} content-length bytes");
                }
                let need = length - body_bytes.len();
                body_bytes.extend_from_slice(&chunk[..read.min(need)]);
                if body_bytes.len() > RESPONSE_MAX_BYTES {
                    bail!("response exceeds {RESPONSE_MAX_BYTES} bytes");
                }
            }
        }
        BodyFraming::Chunked => {
            let mut decoder = ChunkDecoder::new();
            decoder.feed(&raw[consumed..], &mut body_bytes)?;
            while !decoder.is_done() {
                let read = stream.read(&mut chunk).await?;
                if read == 0 {
                    bail!("connection closed inside a chunked body");
                }
                decoder.feed(&chunk[..read], &mut body_bytes)?;
                if body_bytes.len() > RESPONSE_MAX_BYTES {
                    bail!("response exceeds {RESPONSE_MAX_BYTES} bytes");
                }
            }
        }
        BodyFraming::ReadToEof => {
            body_bytes.extend_from_slice(&raw[consumed..]);
            loop {
                let read = stream.read(&mut chunk).await?;
                if read == 0 {
                    break;
                }
                body_bytes.extend_from_slice(&chunk[..read]);
                if body_bytes.len() > RESPONSE_MAX_BYTES {
                    bail!("response exceeds {RESPONSE_MAX_BYTES} bytes");
                }
            }
        }
    }
    Ok((head.status, body_bytes))
}

enum StreamFraming {
    Chunked(ChunkDecoder),
    ReadToEof,
}

/// A live `text/event-stream` connection yielding parsed [`SseFrame`]s.
pub struct SseStream {
    stream: TcpStream,
    framing: StreamFraming,
    decoded: Vec<u8>,
    decoded_offset: usize,
    parser: SseParser,
    ended: bool,
}

impl SseStream {
    /// Open the stream: send the GET, parse the head, and verify the server
    /// answered with `text/event-stream`.
    pub(crate) async fn open(base: &Url, path_and_query: &str) -> Result<Self> {
        let (host, port) = loopback_authority(base)?;
        let mut stream = connect(base).await?;
        let request_bytes = write_request_head(
            "GET",
            path_and_query,
            &host,
            port,
            "text/event-stream",
            None,
            false,
        );
        stream.write_all(&request_bytes).await?;

        let mut raw = Vec::new();
        let mut chunk = vec![0_u8; READ_BUFFER_BYTES];
        let (head, consumed) = loop {
            if let Some(parsed) = parse_response_head(&raw)? {
                break parsed;
            }
            let read = stream.read(&mut chunk).await?;
            if read == 0 {
                bail!("connection closed before the event stream head");
            }
            raw.extend_from_slice(&chunk[..read]);
        };
        if head.status != 200 {
            bail!("event stream request answered with status {}", head.status);
        }
        let content_type = head.header("content-type").unwrap_or_default();
        if !content_type.starts_with("text/event-stream") {
            bail!("event stream answered with content-type {content_type:?}");
        }
        let mut framing = match body_framing(&head)? {
            BodyFraming::Chunked => StreamFraming::Chunked(ChunkDecoder::new()),
            BodyFraming::ReadToEof => StreamFraming::ReadToEof,
            BodyFraming::Length(_) => {
                bail!("event stream answered with a fixed content-length");
            }
        };
        let mut decoded = Vec::new();
        decode_into(&mut framing, &raw[consumed..], &mut decoded)?;
        Ok(Self {
            stream,
            framing,
            decoded,
            decoded_offset: 0,
            parser: SseParser::default(),
            ended: false,
        })
    }

    /// Next dispatched frame; `None` once the server ends the stream cleanly.
    pub async fn next_frame(&mut self) -> Result<Option<SseFrame>> {
        loop {
            while let Some(line) = self.take_line()? {
                if let Some(frame) = self.parser.feed_line(&line)? {
                    return Ok(Some(frame));
                }
            }
            if self.ended {
                return Ok(None);
            }
            let mut chunk = vec![0_u8; READ_BUFFER_BYTES];
            let read = self.stream.read(&mut chunk).await?;
            if read == 0 {
                if let StreamFraming::Chunked(decoder) = &self.framing
                    && !decoder.is_done()
                {
                    bail!("connection closed inside a chunked event stream");
                }
                self.ended = true;
                continue;
            }
            decode_into(&mut self.framing, &chunk[..read], &mut self.decoded)?;
            if let StreamFraming::Chunked(decoder) = &self.framing
                && decoder.is_done()
            {
                self.ended = true;
            }
        }
    }

    /// Bounded wait for the next frame.
    pub async fn next_frame_timeout(&mut self, timeout: Duration) -> Result<Option<SseFrame>> {
        tokio::time::timeout(timeout, self.next_frame())
            .await
            .context("waiting for the next server-sent event timed out")?
    }

    fn take_line(&mut self) -> Result<Option<String>> {
        let pending = &self.decoded[self.decoded_offset..];
        let Some(newline) = pending.iter().position(|byte| *byte == b'\n') else {
            if pending.len() > SSE_LINE_MAX_BYTES {
                bail!("server-sent event line exceeds {SSE_LINE_MAX_BYTES} bytes");
            }
            if self.decoded_offset > 0 {
                self.decoded.drain(..self.decoded_offset);
                self.decoded_offset = 0;
            }
            return Ok(None);
        };
        let mut line = pending[..newline].to_vec();
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        self.decoded_offset += newline + 1;
        let line = String::from_utf8(line).context("server-sent event line is not UTF-8")?;
        Ok(Some(line))
    }
}

fn decode_into(framing: &mut StreamFraming, input: &[u8], out: &mut Vec<u8>) -> Result<()> {
    match framing {
        StreamFraming::Chunked(decoder) => decoder.feed(input, out),
        StreamFraming::ReadToEof => {
            out.extend_from_slice(input);
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn response_head_parses_incrementally_and_normalizes_names() {
        let wire =
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Extra: a: b\r\n\r\nrest";
        assert!(parse_response_head(&wire[..10]).expect("partial").is_none());
        let (head, consumed) = parse_response_head(wire)
            .expect("parse")
            .expect("complete head");
        assert_eq!(head.status, 200);
        assert_eq!(head.header("content-type"), Some("application/json"));
        assert_eq!(head.header("x-extra"), Some("a: b"));
        assert_eq!(&wire[consumed..], b"rest");
    }

    #[test]
    fn response_head_rejects_folding_missing_colons_and_alien_versions() {
        let folded = b"HTTP/1.1 200 OK\r\na: b\r\n c\r\n\r\n";
        assert!(parse_response_head(folded).is_err());
        let colonless = b"HTTP/1.1 200 OK\r\nbroken header\r\n\r\n";
        assert!(parse_response_head(colonless).is_err());
        let alien = b"HTTP/2 200 OK\r\n\r\n";
        assert!(parse_response_head(alien).is_err());
    }

    #[test]
    fn body_framing_prefers_declared_lengths_and_refuses_conflicts() {
        let length = ResponseHead {
            status: 200,
            headers: vec![("content-length".into(), "12".into())],
        };
        assert!(matches!(
            body_framing(&length).expect("length"),
            BodyFraming::Length(12)
        ));
        let chunked = ResponseHead {
            status: 200,
            headers: vec![("transfer-encoding".into(), "chunked".into())],
        };
        assert!(matches!(
            body_framing(&chunked).expect("chunked"),
            BodyFraming::Chunked
        ));
        let neither = ResponseHead {
            status: 200,
            headers: vec![],
        };
        assert!(matches!(
            body_framing(&neither).expect("eof"),
            BodyFraming::ReadToEof
        ));
        let doubled = ResponseHead {
            status: 200,
            headers: vec![
                ("content-length".into(), "1".into()),
                ("content-length".into(), "2".into()),
            ],
        };
        assert!(body_framing(&doubled).is_err());
        let gzip = ResponseHead {
            status: 200,
            headers: vec![("transfer-encoding".into(), "gzip, chunked".into())],
        };
        assert!(body_framing(&gzip).is_err());
    }

    #[test]
    fn chunk_decoder_survives_any_split_point() {
        let wire =
            b"4\r\nWiki\r\n7;ext=1\r\npedia i\r\na\r\nn\r\nchunks.\r\n0\r\ntrailer: x\r\n\r\n";
        for split in 0..wire.len() {
            let mut decoder = ChunkDecoder::new();
            let mut out = Vec::new();
            decoder.feed(&wire[..split], &mut out).expect("first half");
            decoder.feed(&wire[split..], &mut out).expect("second half");
            assert!(decoder.is_done(), "split at {split}");
            assert_eq!(out, b"Wikipedia in\r\nchunks.", "split at {split}");
        }
    }

    #[test]
    fn chunk_decoder_rejects_bad_size_lines_and_bad_terminators() {
        let mut decoder = ChunkDecoder::new();
        let mut out = Vec::new();
        assert!(decoder.feed(b"zz\r\n", &mut out).is_err());
        let mut decoder = ChunkDecoder::new();
        let mut out = Vec::new();
        assert!(decoder.feed(b"1\r\nAB", &mut out).is_err());
    }

    #[test]
    fn sse_parser_joins_data_ignores_comments_and_skips_empty_dispatch() {
        let mut parser = SseParser::default();
        assert_eq!(parser.feed_line(": keepalive").expect("comment"), None);
        assert_eq!(parser.feed_line("").expect("empty"), None);
        assert_eq!(parser.feed_line("event: bus").expect("event"), None);
        assert_eq!(parser.feed_line("id: evt_9").expect("id"), None);
        assert_eq!(parser.feed_line("data: line one").expect("data"), None);
        assert_eq!(parser.feed_line("data:line two").expect("data"), None);
        let frame = parser
            .feed_line("")
            .expect("dispatch")
            .expect("frame present");
        assert_eq!(frame.event.as_deref(), Some("bus"));
        assert_eq!(frame.id.as_deref(), Some("evt_9"));
        assert_eq!(frame.data, "line one\nline two");
        // The event-type buffer resets after dispatch; the id persists.
        assert_eq!(parser.feed_line("data: next").expect("data"), None);
        let next = parser.feed_line("").expect("dispatch").expect("frame");
        assert_eq!(next.event, None);
        assert_eq!(next.id.as_deref(), Some("evt_9"));
    }

    #[test]
    fn loopback_authority_accepts_loopback_only() {
        let ok = Url::parse("http://127.0.0.1:4096").expect("url");
        assert_eq!(
            loopback_authority(&ok).expect("loopback"),
            ("127.0.0.1".to_string(), 4096)
        );
        loopback_authority(&Url::parse("http://localhost:1").expect("url")).expect("localhost");
        loopback_authority(&Url::parse("http://[::1]:4096").expect("url")).expect("ipv6 loopback");
        assert!(loopback_authority(&Url::parse("http://10.0.0.1:4096").expect("url")).is_err());
        assert!(loopback_authority(&Url::parse("http://opencode.ai").expect("url")).is_err());
        assert!(loopback_authority(&Url::parse("https://127.0.0.1:4096").expect("url")).is_err());
    }

    #[test]
    fn path_segments_stay_inside_the_identifier_alphabet() {
        validate_path_segment("ses_01J9ABC").expect("session id");
        validate_path_segment("per-1.2").expect("dots and dashes");
        for bad in ["", "a/b", "a?b", "a#b", "a b", "..", ".", "a%2f"] {
            assert!(validate_path_segment(bad).is_err(), "{bad:?} must fail");
        }
    }
}
