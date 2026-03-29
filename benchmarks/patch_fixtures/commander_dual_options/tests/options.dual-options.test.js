const test = require('node:test');
const assert = require('node:assert/strict');

const { Option, DualOptions } = require('../lib/option.js');

test('unrelated positive and negative options are not treated as dual options', () => {
  const helper = new DualOptions([new Option('--one'), new Option('--no-two')]);
  assert.equal(helper.dualOptions.size, 0);
});

test('related positive and negative options are treated as dual options', () => {
  const helper = new DualOptions([new Option('--one'), new Option('--no-one')]);
  assert.equal(helper.dualOptions.size, 1);
});
