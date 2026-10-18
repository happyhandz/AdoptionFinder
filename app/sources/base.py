class Source:
    """A listing source. fetch(region) returns normalized listing dicts
    (keys = db.LISTING_COLS) for dogs within region = {zip, lat, lon, radius}."""

    name = "base"
    manual = False  # True: fed by imports, never polled

    def enabled(self):
        return True

    def fetch(self, region, db=None):
        raise NotImplementedError
