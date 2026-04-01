function stripColor(str) {
  // Derived from commander.stripColor for patch benchmarking.
  // eslint-disable-next-line no-control-regex
  const sgrPattern = /\x1b\[[0-9;]*m/g;
  return str.replace(sgrPattern, '');
}

exports.stripColor = stripColor;
