class Argument {
  constructor(name, description) {
    this.description = description || '';
    this.variadic = false;

    switch (name[0]) {
      case '<':
        this.required = true;
        this._name = name.slice(1, -1);
        break;
      case '[':
        this.required = false;
        this._name = name.slice(1, -1);
        break;
      default:
        this.required = true;
        this._name = name;
        break;
    }

    if (this._name.length > 3 && this._name.slice(-3) === '...') {
      this.variadic = true;
      this._name = this._name.slice(0, -3);
    }
  }

  name() {
    return this._name;
  }
}

function humanReadableArgName(arg) {
  const nameOutput = arg.name() + (arg.variadic === true ? '...' : '');

  return arg.required ? '<' + nameOutput + '>' : '[' + nameOutput + ']';
}

exports.Argument = Argument;
exports.humanReadableArgName = humanReadableArgName;
