def process_data(data):
    if not data:
        return None
    for item in data:
        print(item)
    return True


class DataManager:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def process_data(self, data):
        return [x * 2 for x in data]
