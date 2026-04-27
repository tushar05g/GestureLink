import math
import time

class OneEuroFilter:
    """
    The One Euro Filter is a first-order low-pass filter with an adaptive cutoff frequency.
    It is specifically designed for low-latency signal filtering like cursor movement.
    """
    def __init__(self, freq, mincutoff=1.0, beta=0.007, dcutoff=1.0):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev = None
        self.dx_prev = None

    def __call__(self, x, timestamp=None):
        if self.x_prev is None:
            self.x_prev = x
            self.dx_prev = 0.0
            return x

        # Calculate velocity
        dx = (x - self.x_prev) * self.freq
        
        # Filter velocity
        edx = self._low_pass_filter(dx, self.dx_prev, self._alpha(self.freq, self.dcutoff))
        self.dx_prev = edx

        # Filter signal with adaptive cutoff based on velocity
        cutoff = self.mincutoff + self.beta * abs(edx)
        alpha = self._alpha(self.freq, cutoff)
        
        filtered_x = self._low_pass_filter(x, self.x_prev, alpha)
        self.x_prev = filtered_x
        
        return filtered_x

    def _alpha(self, freq, cutoff):
        te = 1.0 / freq
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def _low_pass_filter(self, x, prev_x, alpha):
        return alpha * x + (1.0 - alpha) * prev_x
