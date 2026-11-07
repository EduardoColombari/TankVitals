#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

function compareSection(manifest, lockRoot, section, errors) {
  const expected = manifest[section] || {};
  const actual = lockRoot[section] || {};
  const names = new Set([...Object.keys(expected), ...Object.keys(actual)]);
  for (const name of names) {
    if (expected[name] !== actual[name]) {
      errors.push(`${section} entry differs: ${name}`);
    }
  }
}

function validateManifests(manifest, lock) {
  const errors = [];
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    return ["package.json must contain a JSON object"];
  }
  if (!lock || typeof lock !== "object" || Array.isArray(lock)) {
    return ["package-lock.json must contain a JSON object"];
  }

  const lockRoot = lock.packages && lock.packages[""];
  if (!lockRoot || typeof lockRoot !== "object" || Array.isArray(lockRoot)) {
    return ['package-lock.json is missing the root packages[""] entry'];
  }

  for (const field of ["name", "version"]) {
    if (manifest[field] !== lock[field] || manifest[field] !== lockRoot[field]) {
      errors.push(`root ${field} differs between package.json and package-lock.json`);
    }
  }
  for (const section of ["dependencies", "devDependencies", "optionalDependencies"]) {
    compareSection(manifest, lockRoot, section, errors);
  }
  return errors;
}

function readJson(filePath, label) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    throw new Error(`could not parse ${label} as JSON`);
  }
}

function main(argv) {
  const baseDir = process.cwd();
  if (argv[2] === "--has-test-script") {
    try {
      const manifest = readJson(path.join(baseDir, "package.json"), "package.json");
      process.stdout.write(manifest.scripts && manifest.scripts.test ? "true\n" : "false\n");
      return 0;
    } catch (error) {
      process.stderr.write(`FAIL: frontend manifest validation: ${error.message}\n`);
      return 1;
    }
  }
  const packagePath = argv[2] || path.join(baseDir, "package.json");
  const lockPath = argv[3] || path.join(baseDir, "package-lock.json");
  let manifest;
  let lock;
  try {
    manifest = readJson(packagePath, "package.json");
    lock = readJson(lockPath, "package-lock.json");
  } catch (error) {
    process.stderr.write(`FAIL: frontend manifest validation: ${error.message}\n`);
    return 1;
  }

  const errors = validateManifests(manifest, lock);
  if (errors.length) {
    for (const error of errors) process.stderr.write(`FAIL: ${error}\n`);
    return 1;
  }
  process.stdout.write("PASS: package.json and package-lock.json root metadata and dependency pins match\n");
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv);
module.exports = { validateManifests };
