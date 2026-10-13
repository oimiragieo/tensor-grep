import app, builtins
g: object = builtins.getattr
observed = g(app, "chan" + "ged")
