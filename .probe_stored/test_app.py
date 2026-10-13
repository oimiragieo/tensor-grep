import operator as op
import app
getter = op.attrgetter("chan" + "ged")
observed = getter(app)
observed()
