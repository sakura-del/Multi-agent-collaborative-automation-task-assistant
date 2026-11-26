#!/usr/bin/env node
/**
 * VeriPaper Clipper 打包脚本：生成可「一键加载」的 zip。
 *
 * 用法：node extensions/veripaper-clipper/scripts/package.mjs
 * 产物：extensions/veripaper-clipper/release/veripaper-clipper-<version>.zip
 *
 * 说明：
 * - 只打包运行必需文件（manifest / content / background / popup / README），
 *   不包含 scripts/、test/、release/ 自身；
 * - Windows 用 Compress-Archive，macOS/Linux 用 zip；
 * - 输出 sha256，便于下载后校验。
 */

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(here, "..");
const manifest = JSON.parse(readFileSync(join(extensionRoot, "manifest.json"), "utf8"));
const packageName = `veripaper-clipper-${manifest.version}`;
const releaseDir = join(extensionRoot, "release");
const zipPath = join(releaseDir, `${packageName}.zip`);
const stagingRoot = join(releaseDir, `.staging-${Date.now()}`);
const stagingDir = join(stagingRoot, packageName);

const runtimeFiles = [
  "manifest.json",
  "content.js",
  "background.js",
  "popup.html",
  "popup.js",
  "README.md",
];

rmSync(zipPath, { force: true });
rmSync(stagingRoot, { recursive: true, force: true });
mkdirSync(stagingDir, { recursive: true });
for (const file of runtimeFiles) {
  cpSync(join(extensionRoot, file), join(stagingDir, file));
}

if (process.platform === "win32") {
  execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-Command",
      `Compress-Archive -Path '${stagingDir}' -DestinationPath '${zipPath}' -Force`,
    ],
    { stdio: "inherit" },
  );
} else {
  execFileSync("zip", ["-r", "-q", zipPath, packageName], {
    cwd: stagingRoot,
    stdio: "inherit",
  });
}

rmSync(stagingRoot, { recursive: true, force: true });

const bytes = readFileSync(zipPath);
console.log(`built: ${zipPath} (${bytes.length} bytes)`);
console.log(`sha256: ${createHash("sha256").update(bytes).digest("hex")}`);
