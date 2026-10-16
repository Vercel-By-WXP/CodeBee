'use strict';

const fs = require('node:fs');

const PATCH_MARKER = '; codebee-installer-details-v1';

function optionalMacro(name) {
  return [`!ifmacrodef ${name}`, `  !insertmacro ${name}`, '!endif'].join('\n');
}

function replaceRequired(source, needle, replacement, label) {
  const index = source.indexOf(needle);
  if (index === -1) throw new Error(`electron-builder installSection.nsh 缺少预期锚点：${label}`);
  return `${source.slice(0, index)}${replacement}${source.slice(index + needle.length)}`;
}

function patchNsisInstallSectionSource(source) {
  const sourceEol = source.includes('\r\n') ? '\r\n' : '\n';
  let patched = source.replaceAll('\r\n', '\n').replaceAll('\r', '\n');
  if (patched.includes(PATCH_MARKER)) return source;

  const detailsAnchor = '${IfNot} ${Silent}\n  SetDetailsPrint none\n${endif}';
  patched = replaceRequired(patched, detailsAnchor, [
    PATCH_MARKER,
    '# Show both electron-builder file entries and CodeBee install phases.',
    '${IfNot} ${Silent}',
    '  SetDetailsPrint listonly',
    '${endif}',
    optionalMacro('customInstallSectionStarted'),
  ].join('\n'), 'SetDetailsPrint none');

  const cleanupStartAnchor = '!insertmacro uninstallOldVersion SHELL_CONTEXT\n!insertmacro handleUninstallResult SHELL_CONTEXT';
  patched = replaceRequired(patched, cleanupStartAnchor,
    `${optionalMacro('customInstallCleanupStarted')}\n${cleanupStartAnchor}`, 'SHELL_CONTEXT cleanup');

  const cleanupEndAnchor = '${if} $installMode == "all"\n  !insertmacro uninstallOldVersion HKEY_CURRENT_USER\n  !insertmacro handleUninstallResult HKEY_CURRENT_USER\n${endIf}';
  patched = replaceRequired(patched, cleanupEndAnchor,
    `${cleanupEndAnchor}\n${optionalMacro('customInstallCleanupCompleted')}`, 'HKEY_CURRENT_USER cleanup');

  const extractAnchor = '!insertmacro installApplicationFiles';
  patched = replaceRequired(patched, extractAnchor,
    `${optionalMacro('customInstallExtractStarted')}\n${extractAnchor}\n${optionalMacro('customInstallExtractCompleted')}`, 'application extraction');

  const shortcutsAnchor = '!insertmacro addStartMenuLink $keepShortcuts\n!insertmacro addDesktopLink $keepShortcuts';
  patched = replaceRequired(patched, shortcutsAnchor,
    `${optionalMacro('customInstallShortcutsStarted')}\n${shortcutsAnchor}\n${optionalMacro('customInstallShortcutsCompleted')}`, 'shortcut installation');

  return sourceEol === '\r\n' ? patched.replaceAll('\n', '\r\n') : patched;
}

async function patchNsisInstallSectionFile(filePath) {
  const originalBuffer = await fs.promises.readFile(filePath);
  const source = originalBuffer.toString('utf8');
  const patched = patchNsisInstallSectionSource(source);
  if (patched === source) return { changed: false, originalSource: null };
  await fs.promises.writeFile(filePath, patched, 'utf8');
  return { changed: true, originalSource: originalBuffer };
}

function restoreNsisInstallSectionFileSync({ filePath, originalSource }) {
  if (originalSource != null) fs.writeFileSync(filePath, originalSource);
}

module.exports = {
  PATCH_MARKER,
  patchNsisInstallSectionSource,
  patchNsisInstallSectionFile,
  restoreNsisInstallSectionFileSync,
};
