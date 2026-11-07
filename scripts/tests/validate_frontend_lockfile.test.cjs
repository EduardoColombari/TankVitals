"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { validateManifests } = require("./validate_frontend_lockfile.cjs");

function consistentFixture() {
  const manifest = {
    name: "frontend",
    version: "0.0.0",
    dependencies: { vue: "^3.5.41" },
    devDependencies: { vite: "^8.2.2" },
  };
  const lock = {
    name: "frontend",
    version: "0.0.0",
    lockfileVersion: 3,
    packages: {
      "": {
        name: "frontend",
        version: "0.0.0",
        dependencies: { vue: "^3.5.41" },
        devDependencies: { vite: "^8.2.2" },
      },
      "node_modules/vue": { version: "3.5.41" },
    },
  };
  return { manifest, lock };
}

test("accepts npm lockfile v3 root package with an empty property name", () => {
  const { manifest, lock } = consistentFixture();
  assert.deepEqual(validateManifests(manifest, lock), []);
});

test("detects a dependency version mismatch", () => {
  const { manifest, lock } = consistentFixture();
  lock.packages[""].dependencies.vue = "^3.4.0";
  assert.ok(validateManifests(manifest, lock).some((message) => message.includes("vue")));
});

test("detects a dependency missing from the lock root", () => {
  const { manifest, lock } = consistentFixture();
  delete lock.packages[""].dependencies.vue;
  assert.ok(validateManifests(manifest, lock).some((message) => message.includes("vue")));
});

test("detects mismatched root package metadata", () => {
  const { manifest, lock } = consistentFixture();
  lock.packages[""].version = "1.0.0";
  assert.ok(validateManifests(manifest, lock).some((message) => message.includes("root version")));
});

test("detects a missing empty-name root entry", () => {
  const { manifest, lock } = consistentFixture();
  delete lock.packages[""];
  assert.ok(validateManifests(manifest, lock).some((message) => message.includes('packages[""]')));
});
