class ProfileCache:
    def __init__(self):
        self.values = {}

    def get_or_set(self, key, loader):
        if key not in self.values:
            self.values[key] = loader()
        return self.values[key]
