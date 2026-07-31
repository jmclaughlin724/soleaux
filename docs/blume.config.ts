import { defineConfig } from "blume";

export default defineConfig({
  title: "Soleaux",
  description:
    "Fast, AI-ready, zero-config repository intelligence for bounded evidence, explicit coverage, semantic navigation, and safe editor workflows.",

  content: {
    sources: [
      {
        prefix: "guides",
        root: "../src/soleaux/resources/docs",
        type: "filesystem",
      },
    ],
  },

  theme: {
    accent: "#0d9488",
    fonts: {
      body: "geist",
      display: "geist",
      mono: "geist-mono",
    },
    mode: "system",
    radius: "md",
  },

  search: {
    provider: "orama",
  },

  markdown: {
    imageZoom: true,
    code: {
      icons: true,
      wrap: false,
    },
    codeBlocks: {
      theme: {
        light: "github-light",
        dark: "github-dark",
      },
    },
  },

  ai: {
    llmsTxt: true,
    mcp: {
      enabled: true,
      instructions:
        "Use these docs to understand Soleaux tool contracts, provider configuration, and agent workflows.",
      name: "Soleaux Docs",
      route: "/mcp",
    },
  },

  seo: {
    agentReadability: true,
    contentSignals: {
      aiInput: true,
      aiTrain: false,
      search: true,
    },
    og: { enabled: true },
    rss: { enabled: false },
    sitemap: true,
    robots: true,
    structuredData: true,
  },

  github: {
    branch: "main",
    dir: "docs",
    owner: "jmclaughlin724",
    repo: "soleaux",
  },

  deployment: {
    adapter: "vercel",
    output: "server",
  },
});
