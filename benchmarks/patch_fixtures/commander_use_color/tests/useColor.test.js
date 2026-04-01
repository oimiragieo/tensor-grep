const test = require('node:test');
const assert = require('node:assert/strict');

const { useColor } = require('../lib/command.js');

test('useColor respects modern color environment variable conventions', async (t) => {
  const holdNoColor = process.env.NO_COLOR;
  const holdForceColor = process.env.FORCE_COLOR;
  const holdCliColorForce = process.env.CLICOLOR_FORCE;

  const restore = () => {
    if (holdNoColor === undefined) delete process.env.NO_COLOR;
    else process.env.NO_COLOR = holdNoColor;

    if (holdForceColor === undefined) delete process.env.FORCE_COLOR;
    else process.env.FORCE_COLOR = holdForceColor;

    if (holdCliColorForce === undefined) delete process.env.CLICOLOR_FORCE;
    else process.env.CLICOLOR_FORCE = holdCliColorForce;
  };

  t.after(restore);

  delete process.env.NO_COLOR;
  delete process.env.FORCE_COLOR;
  delete process.env.CLICOLOR_FORCE;
  assert.equal(useColor(), undefined);

  process.env.NO_COLOR = '';
  assert.equal(useColor(), undefined);

  process.env.NO_COLOR = '1';
  assert.equal(useColor(), false);

  delete process.env.NO_COLOR;
  process.env.CLICOLOR_FORCE = '';
  assert.equal(useColor(), true);

  delete process.env.CLICOLOR_FORCE;
  process.env.FORCE_COLOR = '0';
  assert.equal(useColor(), false);
});
