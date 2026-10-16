import { createHash } from 'node:crypto';
import { copyFile, mkdir, readdir, readFile, stat, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const ASSET_PATTERN = /^CodeBee-(\d+\.\d+\.\d+)-(win-x64|mac-(?:arm64|x64)|linux-x64)\.(exe|dmg|zip|AppImage|deb|rpm)$/;
const REQUIRED_ASSETS = [
  'win-x64:exe', 'mac-arm64:dmg', 'mac-arm64:zip', 'mac-x64:dmg', 'mac-x64:zip',
  'linux-x64:AppImage', 'linux-x64:deb', 'linux-x64:rpm',
];

async function walkFiles(root) {
  const result = [];
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) await visit(fullPath);
      else if (entry.isFile()) result.push(fullPath);
    }
  }
  await visit(root);
  return result;
}

async function sha512(filePath) {
  const hash = createHash('sha512');
  hash.update(await readFile(filePath));
  return hash.digest('hex');
}

async function sha256(filePath) {
  const hash = createHash('sha256');
  hash.update(await readFile(filePath));
  return hash.digest('hex');
}

function versionOf(value) {
  const match = String(value || '').replace(/^v/i, '').match(/^(\d+\.\d+\.\d+)$/);
  return match?.[1] || '';
}

function assetDescriptor(name) {
  const match = name.match(ASSET_PATTERN);
  if (!match) return null;
  return { name, version: match[1], target: match[2], format: match[3] };
}

async function loadJson(filePath) {
  return JSON.parse(await readFile(filePath, 'utf8'));
}

export async function collectRelease({
  inputDir,
  outputDir,
  tag,
  rootPackagePath = path.resolve(inputDir, '..', 'package.json'),
  desktopPackagePath = path.resolve(inputDir, '..', 'desktop', 'package.json'),
  requireComplete = true,
  environment = process.env,
} = {}) {
  if (!inputDir || !outputDir || !tag) throw new Error('inputDir、outputDir 和 tag 都是必需的');
  const rootPackage = await loadJson(rootPackagePath);
  const desktopPackage = await loadJson(desktopPackagePath);
  const version = versionOf(rootPackage.version);
  if (!version || version !== versionOf(desktopPackage.version)) throw new Error('root/desktop 版本不一致');
  const tagVersion = versionOf(tag);
  if (tagVersion !== version) throw new Error(`tag 版本 ${tagVersion || tag} 与包版本 ${version} 不一致`);

  const files = await walkFiles(inputDir);
  const assets = [];
  const seen = new Set();
  for (const filePath of files) {
    const name = path.basename(filePath);
    const descriptor = assetDescriptor(name);
    if (!descriptor) {
      if (name.startsWith('CodeBee-')) {
        throw new Error(`产物文件名不符合规范：${name}`);
      }
      continue;
    }
    if (seen.has(name)) throw new Error(`发现重复产物文件名：${name}`);
    if (descriptor.version !== version) throw new Error(`产物 ${name} 与包版本 ${version} 不一致`);
    seen.add(name);
    assets.push({ ...descriptor, sourcePath: filePath });
  }
  if (assets.length === 0) throw new Error('未找到规范桌面产物');
  if (requireComplete) {
    const actual = new Set(assets.map(asset => `${asset.target}:${asset.format}`));
    const missing = REQUIRED_ASSETS.filter(key => !actual.has(key));
    if (missing.length) throw new Error(`缺少桌面产物：${missing.join(', ')}`);
  }

  await mkdir(outputDir, { recursive: true });
  const records = [];
  for (const asset of assets.sort((a, b) => a.name.localeCompare(b.name))) {
    const destination = path.join(outputDir, asset.name);
    await copyFile(asset.sourcePath, destination);
    const info = await stat(destination);
    records.push({
      name: asset.name,
      target: asset.target,
      format: asset.format,
      size: info.size,
      sha256: await sha256(destination),
      sha512: await sha512(destination),
    });
  }
  const generatedAt = environment.SOURCE_DATE_EPOCH
    ? new Date(Number(environment.SOURCE_DATE_EPOCH) * 1000).toISOString()
    : new Date().toISOString();
  const manifest = {
    schemaVersion: 1,
    product: 'CodeBee',
    version,
    tag,
    assets: records,
  };
  const buildRecord = {
    schemaVersion: 1,
    product: 'CodeBee',
    version,
    tag,
    commit: environment.GITHUB_SHA || null,
    runId: environment.GITHUB_RUN_ID || null,
    generatedAt,
    requiredTargets: REQUIRED_ASSETS,
  };
  await writeFile(path.join(outputDir, 'SHA512SUMS.txt'), records.map(asset => `${asset.sha512}  ${asset.name}`).join('\n') + '\n', 'utf8');
  await writeFile(path.join(outputDir, 'SHA256SUMS.txt'), records.map(asset => `${asset.sha256}  ${asset.name}`).join('\n') + '\n', 'utf8');
  await writeFile(path.join(outputDir, 'desktop-builds.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  await writeFile(path.join(outputDir, 'build-record.json'), `${JSON.stringify(buildRecord, null, 2)}\n`, 'utf8');
  return { version, tag, assets: records, manifest, buildRecord };
}

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  collectRelease({
    inputDir: option('--input', 'artifacts'),
    outputDir: option('--output', 'release'),
    tag: option('--tag', process.env.GITHUB_REF_NAME || ''),
  }).then(result => console.log(`Collected ${result.assets.length} desktop assets for ${result.tag}.`))
    .catch(error => { console.error(error.message); process.exitCode = 1; });
}
