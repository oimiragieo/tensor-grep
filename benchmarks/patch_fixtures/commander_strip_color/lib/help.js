function stripColor(str) {
  // Derived from commander.stripColor for patch benchmarking.
  // eslint-disable-next-line no-control-regex
  const sgrPattern = /\x1b\[\d+(;\d+)*m/g;
  return str.replace(sgrPattern, '');
}

exports.stripColor = stripColor;
