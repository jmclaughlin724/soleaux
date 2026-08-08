import { open } from "node:fs/promises";

export async function loadConfig(path: string) {
  const handle = await open(path);
  const parsed = JSON.parse(
