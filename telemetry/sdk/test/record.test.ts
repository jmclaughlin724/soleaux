import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { SoleauxTelemetryClient } from "../src/index.ts";
import type { UsageEvent } from "@soleaux/protocol";

function buildEvent(): UsageEvent {
  return {
    id: "evt-1",
    providerId: "openai",
    modelId: "gpt-5",
    source: "api-response",
    occurredAt: 0,
    usage: {
      inputTokens: 10,
      cachedInputTokens: 0,
      cacheWriteTokens: 0,
      outputTokens: 5,
      reasoningTokens: 0,
      totalTokens: 0,
    },
    performance: {
      requestStartedAt: 1000,
      completedAt: 2000,
      retryCount: 0,
      status: "completed",
    },
  };
}

function isUsageEvent(data: unknown): data is UsageEvent {
  return (
    typeof data === "object" &&
    data !== null &&
    "id" in data &&
    typeof data.id === "string"
  );
}

interface StubResponse {
  status: number;
  body: unknown;
}

async function withDaemonStub(
  handler: (posted: unknown) => StubResponse,
  run: (daemonUrl: string) => Promise<void>
): Promise<void> {
  const server = createServer((request, response) => {
    let raw = "";
    request.on("data", (chunk: Buffer) => {
      raw += chunk.toString("utf-8");
    });
    request.on("end", () => {
      const { status, body } = handler(JSON.parse(raw));
      response.writeHead(status, { "content-type": "application/json" });
      response.end(typeof body === "string" ? body : JSON.stringify(body));
    });
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("stub server has no bound port");
  }
  try {
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise<void>((resolve) => {
      server.close(() => {
        resolve();
      });
    });
  }
}

void test("recordUsage returns the server-normalized record from a 201 echo", async () => {
  await withDaemonStub(
    (posted) => {
      if (!isUsageEvent(posted)) {
        throw new Error("stub received a non-record body");
      }
      return {
        status: 201,
        body: {
          ...posted,
          occurredAt: 9999,
          usage: { ...posted.usage, totalTokens: 15 },
        },
      };
    },
    async (daemonUrl) => {
      const client = new SoleauxTelemetryClient({ daemonUrl });
      const recorded = await client.recordUsage(buildEvent());
      assert.equal(recorded.occurredAt, 9999);
      assert.equal(recorded.usage.totalTokens, 15);
    }
  );
});

void test("recordUsage throws with the status in the message on a 500", async () => {
  await withDaemonStub(
    () => ({ status: 500, body: "daemon exploded" }),
    async (daemonUrl) => {
      const client = new SoleauxTelemetryClient({ daemonUrl });
      await assert.rejects(client.recordUsage(buildEvent()), /500/u);
    }
  );
});

void test("recordUsage returns the daemon's existing record on a 409 dedupe", async () => {
  const stored = new Map<string, UsageEvent>();
  await withDaemonStub(
    (posted) => {
      if (!isUsageEvent(posted)) {
        throw new Error("stub received a non-record body");
      }
      const existing = stored.get(posted.id);
      if (existing !== undefined) {
        return { status: 409, body: existing };
      }
      const normalized = { ...posted, occurredAt: 4242 };
      stored.set(posted.id, normalized);
      return { status: 201, body: normalized };
    },
    async (daemonUrl) => {
      const client = new SoleauxTelemetryClient({ daemonUrl });
      const first = await client.recordUsage(buildEvent());
      assert.equal(first.occurredAt, 4242);
      const duplicate = await client.recordUsage({
        ...buildEvent(),
        occurredAt: 7777,
      });
      assert.equal(duplicate.occurredAt, 4242);
    }
  );
});

void test("recordUsage falls back to the merged local record on a non-record echo", async () => {
  await withDaemonStub(
    () => ({ status: 201, body: "ok" }),
    async (daemonUrl) => {
      const client = new SoleauxTelemetryClient({
        daemonUrl,
        sessionId: "session-1",
      });
      const event = buildEvent();
      const recorded = await client.recordUsage(event);
      assert.equal(recorded.sessionId, "session-1");
      assert.equal(recorded.id, event.id);
      assert.equal(recorded.occurredAt, event.occurredAt);
      assert.deepEqual(recorded.usage, event.usage);
    }
  );
});
