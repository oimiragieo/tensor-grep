const test = require('node:test');
const assert = require('node:assert/strict');

const { stripColor } = require('../lib/help.js');

const ESC = '\u001b';
const CSI = ESC + '[';

test('stripColor removes implicit reset sequences', () => {
  assert.equal(stripColor(`${CSI}mtext${CSI}0m`), 'text');
});
