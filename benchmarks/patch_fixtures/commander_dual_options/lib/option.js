class Option {
  constructor(flags) {
    this.flags = flags;
    this.negate = flags.includes('--no-');
  }

  attributeName() {
    return this.flags.replace(/^--no-/, '').replace(/^--/, '');
  }
}

class DualOptions {
  constructor(options) {
    this.positiveOptions = new Map();
    this.negativeOptions = new Map();
    this.dualOptions = new Set();
    options.forEach((option) => {
      if (option.negate) {
        this.negativeOptions.set(option.attributeName(), option);
      } else {
        this.positiveOptions.set(option.attributeName(), option);
      }
    });
    this.negativeOptions.forEach((value, key) => {
      if (this.positiveOptions.has(key)) {
        this.dualOptions.add(key);
      }
    });
  }
}

exports.Option = Option;
exports.DualOptions = DualOptions;
