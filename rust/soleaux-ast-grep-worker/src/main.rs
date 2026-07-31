//! JSONL loop: read one request per stdin line, write one response per
//! stdout line, log failures to stderr only, exit 0 on EOF or shutdown.

use std::io::{BufWriter, Write};

use serde_json::Value;
use soleaux_ast_grep_worker::{Frame, Outcome, handle_frame, oversized_frame_reply, read_frame};

fn write_reply(writer: &mut impl Write, reply: &Value) -> std::io::Result<()> {
    serde_json::to_writer(&mut *writer, reply)?;
    writer.write_all(b"\n")?;
    writer.flush()
}

fn serve(reader: &mut impl std::io::BufRead, writer: &mut impl Write) -> std::io::Result<()> {
    loop {
        let Some(frame) = read_frame(reader)? else {
            return Ok(());
        };
        match frame {
            Frame::Oversized => write_reply(writer, &oversized_frame_reply())?,
            Frame::Line(line) => match handle_frame(&line) {
                Outcome::Reply(reply) => write_reply(writer, &reply)?,
                Outcome::Shutdown(reply) => {
                    write_reply(writer, &reply)?;
                    return Ok(());
                }
            },
        }
    }
}

fn main() {
    let stdin = std::io::stdin();
    let mut reader = stdin.lock();
    let stdout = std::io::stdout();
    let mut writer = BufWriter::new(stdout.lock());
    if let Err(error) = serve(&mut reader, &mut writer) {
        eprintln!("soleaux-ast-grep-worker: stdio failure: {error}");
        std::process::exit(1);
    }
}
