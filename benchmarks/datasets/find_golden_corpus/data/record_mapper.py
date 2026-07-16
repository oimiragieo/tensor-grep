"""Bridges storage rows and in-memory domain objects."""


class Record:
    def __init__(self, **fields):
        self.fields = fields


def hydrate_row_to_record(row_tuple, column_names):
    paired = zip(column_names, row_tuple, strict=False)
    return Record(**dict(paired))


def flatten_record_to_row(obj, column_names):
    return tuple(obj.fields.get(name) for name in column_names)
