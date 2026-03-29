function useColor() {
  if (process.env.NO_COLOR !== undefined) return false;
  if (process.env.FORCE_COLOR || process.env.CLICOLOR_FORCE) return true;
  return undefined;
}

exports.useColor = useColor;
