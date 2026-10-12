'use strict';

const fs = require('node:fs');
const path = require('node:path');

const MACH_O = new Set(['cffaedfe', 'cefaedfe', 'feedfacf', 'feedface', 'cafebabe', 'bebafeca']);

function machOBinaries(root, relativeRoot, result = []) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    const relative = path.join(relativeRoot, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) machOBinaries(full, relative, result);
    else {
      const handle = fs.openSync(full, 'r');
      try {
        const magic = Buffer.alloc(4);
        if (fs.readSync(handle, magic, 0, 4, 0) === 4 && MACH_O.has(magic.toString('hex'))) result.push(relative);
      } finally { fs.closeSync(handle); }
    }
  }
  return result;
}

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const bundle = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  const relative = 'Contents/Resources/python';
  const binaries = machOBinaries(path.join(bundle, relative), relative);
  // electron-builder 26 uses mac.binaries (not the future mac.sign.binaries).
  context.packager.platformSpecificBuildOptions.binaries = [
    ...(context.packager.platformSpecificBuildOptions.binaries || []), ...binaries,
  ];
  console.log(`Added ${binaries.length} Python Mach-O binaries to the macOS signing list.`);
};
module.exports.machOBinaries = machOBinaries;
