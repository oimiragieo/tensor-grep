const test = require('node:test');
const assert = require('node:assert/strict');

const { Argument, humanReadableArgName } = require('../lib/argument.js');

test('humanReadableArgName formats required arguments with angle brackets', () => {
  const arg = new Argument('<source>', 'input file');
  assert.equal(humanReadableArgName(arg), '<source>');
});

test('humanReadableArgName formats optional variadic arguments with square brackets', () => {
  const arg = new Argument('[files...]', 'extra files');
  assert.equal(humanReadableArgName(arg), '[files...]');
});
