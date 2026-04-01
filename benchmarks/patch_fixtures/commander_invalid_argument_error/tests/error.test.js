const test = require('node:test');
const assert = require('node:assert/strict');

const { InvalidArgumentError } = require('../lib/error.js');

test('InvalidArgumentError uses the commander.invalidArgument code', () => {
  const err = new InvalidArgumentError('failed');
  assert.equal(err.code, 'commander.invalidArgument');
  assert.equal(err.exitCode, 1);
  assert.equal(err.name, 'InvalidArgumentError');
});
