class ConfigError(Exception):
    """Raised when the nDisplay configuration file is missing required data or is malformed."""

    pass


class ImageSetError(Exception):
    """Raised when the rendered image set is incomplete or missing expected viewports/frames."""

    pass

