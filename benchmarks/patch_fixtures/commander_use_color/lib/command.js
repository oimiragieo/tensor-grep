function useColor() {
  const noColor = process.env.NO_COLOR;
  if (noColor !== undefined && noColor !== '') return false;

  const forceColor = process.env.FORCE_COLOR;
  const cliColorForce = process.env.CLICOLOR_FORCE;

  if (forceColor !== undefined) {
    if (forceColor === '0') return false;
    return true;
  }

  if (cliColorForce !== undefined) {
    if (cliColorForce === '0') return false;
    return true;
  }

  return undefined;
}

exports.useColor = useColor;
