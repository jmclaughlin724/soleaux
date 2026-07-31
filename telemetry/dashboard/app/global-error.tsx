"use client";

export default function GlobalError({
  error,
  reset,
}: {
  readonly error: Error & { digest?: string };
  readonly reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main>
          <h2>Soleaux dashboard error</h2>
          <p>{error.message}</p>
          <button onClick={() => reset()} type="button">
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
