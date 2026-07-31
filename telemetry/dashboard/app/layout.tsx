import type { Metadata } from "next";

import "@soleaux/ui/globals.css";

export const metadata: Metadata = {
  title: "Soleaux",
  description: "Local-first observability for AI coding agents",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="bg-background text-foreground min-h-svh antialiased">
        {children}
      </body>
    </html>
  );
}
