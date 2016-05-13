""" Utilities for dealing with NTP time stamps """

__author__ = 'Luke Campbell <LCampbell@ASAScience.com>, Michael Meisinger'

import time
import datetime
import struct
import numbers
try:
    import numpy as np
except ImportError:
    np = None


class NTP4Time(object):
    """
    Utility wrapper for handling time in ntpv4
    Everything is in UTC
    """
    FRAC = np.float32(4294967296.) if np else None
    JAN_1970 = np.uint32(2208988800) if np else None
    JAN_1970_INT = 2208988800
    EPOCH = datetime.datetime(1900, 1, 1)

    ntpv4_timestamp = '! 2I'
    ntpv4_date      = '! 2I Q'

    def __init__(self, date=None):
        """ Can be initialized with a standard unix time stamp """
        #  Is it correct to represent NTP4 internally as datetime?
        if date is None:
            date = time.time()
        if isinstance(date, numbers.Number):
            self._dt = datetime.datetime.utcfromtimestamp(date)
        elif isinstance(date, datetime.datetime):
            self._dt = date
        elif isinstance(date, datetime.date):
            self._dt = datetime.datetime.combine(date, datetime.time())

    @classmethod
    def utcnow(cls):
        return NTP4Time()

    @property
    def year(self):
        return self._dt.year

    @property
    def month(self):
        return self._dt.month

    @property
    def day(self):
        return self._dt.day

    @property
    def hour(self):
        return self._dt.hour

    @property
    def minute(self):
        return self._dt.minute

    @property
    def second(self):
        return self._dt.second

    @property
    def date(self):
        from ion.util.time_utils import IonDate
        return IonDate(self.year, self.month, self.day)

    @property
    def era(self):
        delta = (self._dt - self.EPOCH).total_seconds()
        return np.uint32( int(delta) / 2**32)

    @property
    def seconds(self):
        delta = self._dt - self.EPOCH
        return np.uint32(np.trunc(delta.total_seconds()))

    @seconds.setter
    def seconds(self,value):
        delta = datetime.timedelta(seconds=value)
        self._dt = self.EPOCH + delta

    @property
    def useconds(self):
        delta = self._dt - self.EPOCH
        return np.uint32(np.modf(delta.total_seconds())[0] * 1e6)

    @property
    def microseconds(self):
        return self.useconds

    def __repr__(self):
        return '<%s "%s" at 0x%x>' % (self.__class__.__name__, str(self), id(self))

    def __str__(self):
        return self._dt.isoformat()

    def to_ntp64(self):
        """
        Returns the NTPv4 64bit timestamp as binary (str)
           0                   1                   2                   3
           0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                            Seconds                            |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                            Fraction                           |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        
        """
        delta = (self._dt - self.EPOCH).total_seconds()
        seconds = np.uint32(np.trunc(delta))
        fraction = np.uint32((delta - int(delta)) * 2**32)
        timestamp = struct.pack(self.ntpv4_timestamp, seconds, fraction)
        return timestamp
    
    @classmethod
    def from_ntp64(cls, val):
        """
        Converts a RFC 5905 (NTPv4) compliant 64bit time stamp into an NTP4Time object
        """
        seconds, fraction = struct.unpack(cls.ntpv4_timestamp, val)
        it = cls()
        it.seconds = seconds + (fraction *1e0 / 2**32)
        return it

    def to_ntp_date(self):
        """
        Returns the NTPv4 128bit date timestamp
           0                   1                   2                   3
           0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                           Era Number                          |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                           Era Offset                          |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
          |                                                               |
          |                           Fraction                            |
          |                                                               |
          +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
        """
        delta = (self._dt - self.EPOCH).total_seconds()
        era = int(delta) / (2**32)
        offset = np.uint32(np.trunc(delta)) # Overflow does all the work for us
        fraction = np.uint64((delta - int(delta)) * 2**64)
        ntp_date = struct.pack(self.ntpv4_date, era, offset, fraction)
        return ntp_date

    @classmethod
    def from_ntp_date(cls, value):
        """
        Returns an NTP4Time object based on the 128bit RFC 5905 (NTPv4) Date Format
        """
        era, seconds, fraction = struct.unpack(cls.ntpv4_date, value)
        it = cls()
        it.seconds = (era * 2**32) + seconds + (fraction * 1e0 / 2**64)
        return it

    def to_string(self):
        """
        Creates a hexidecimal string of the NTP time stamp (serialization)
        """
        val = self.to_ntp64()
        assert len(val) == 8
        arr = [0] * 8
        for i in xrange(8):
            arr[i] = '%02x' % ord(val[i])
        retval = ''.join(arr)
        return retval

    def to_extended_string(self):
        """
        Creates a hexidecimal string of the NTP date format (serialization)
        """
        val = self.to_ntp_date()
        assert len(val) == 16
        arr = [0] * 16
        for i in xrange(16):
            arr[i] = '%02x' % ord(val[i])
        retval = ''.join(arr)
        return retval

    def to_np_value(self, dtype="i8"):
        """
        Returns 64bit NTPv4 representation as i8 value.
        """
        val = self.to_ntp64()
        return np.fromstring(val, dtype=dtype)

    @classmethod
    def np_from_string(cls, s, dtype="i8"):
        return np.fromstring(s, dtype=dtype)

    @classmethod
    def from_string(cls, s):
        """
        Creates an NTP4Time object from the serialized time stamp
        """
        assert len(s) == 16
        arr = [0] * 8
        for i in xrange(8):
            arr[i] = chr(int(s[2*i:2*i+2],16))
        retval = ''.join(arr)
        it = cls.from_ntp64(retval)
        return it
    
    @classmethod
    def from_extended_string(cls, s):
        """
        Creates an NTP4Time object from the serialized extended time stamp
        """
        assert len(s) == 32
        arr = [0] * 16 
        for i in xrange(16):
            arr[i] = chr(int(s[2*i:2*i+2],16))
        retval = ''.join(arr)
        it = cls.from_ntp_date(retval)
        return it

    def to_unix(self):
        """
        Returns the unix timestamp for this NTP4Time
        """
        delta = self._dt - self.EPOCH
        return delta.total_seconds() - self.JAN_1970_INT

    @staticmethod
    def htonstr(val):
        import sys
        if sys.byteorder == 'little':
            l = len(val)
            nval = [0] * l
            for i in xrange(l/2):
                nval[i*2]   = val[l - i*2 - 2]
                nval[i*2+1] = val[l - i*2 - 1]
            return ''.join(nval)
        return val

    @staticmethod
    def htonl(val):
        import sys
        val = np.uint32(val)
        if sys.byteorder == 'little':
            return val.byteswap()
        return val

    @staticmethod
    def htonll(val):
        import sys
        val = np.uint64(val)
        if sys.byteorder == 'little':
            return val.byteswap()
        return val

    def __eq__(self, other):
        return isinstance(other, NTP4Time) and self._dt == other._dt

    def __ne__(self, other):
        return not isinstance(other, NTP4Time) or self._dt != other._dt

    def __gt__(self, other):
        return isinstance(other, NTP4Time) and self._dt > other._dt

    def __ge__(self, other):
        return isinstance(other, NTP4Time) and self._dt >= other._dt

    def __lt__(self, other):
        return isinstance(other, NTP4Time) and self._dt < other._dt

    def __le__(self, other):
        return isinstance(other, NTP4Time) and self._dt <= other._dt

    def to_sortable(self):
        """ Returns a long integer maintaining sort order """
        delta = (self._dt - self.EPOCH).total_seconds()
        seconds = np.uint32(np.trunc(delta))
        fraction = np.uint32((delta - int(delta)) * 2**32)
        value = int(seconds) * 2**32 + int(fraction)
        return value
