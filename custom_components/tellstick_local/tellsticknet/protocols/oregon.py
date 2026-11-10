# Local change (tellstick_local): decoders for more Oregon models, ported from
# Telldus' telldus-core ProtocolOregon.cpp. Like the firmware, the TellStick
# Net only frames the message; the fields are decoded here. Unlike Telldus the
# sensor ID is the whole rolling-code byte (Telldus adds its two nibbles).
# Only the field layouts are taken from there; the code is our own.


def _nibbles(data, count):
    """Nibbles of the message, least significant first."""
    value = int(data)
    return [(value >> (4 * i)) & 0xF for i in range(count)]


def _id_sum(model):
    return sum(_nibbles(model, 4))


def _check(nibbles, payload, model):
    """Checksum shared by the v3 decoders: nibble sum in nibbles 0-1 (high first)
    or, with a CRC nibble first, in nibbles 1-2."""
    checksum = (sum(payload) + _id_sum(model)) & 0xFF
    if (checksum >> 4, checksum & 0xF) != (nibbles[0], nibbles[1]):
        raise ValueError("Oregon checksum mismatch")


def _bcd(*digits):
    value = 0
    for digit in digits:
        value = value * 10 + digit
    return value


def _rolling(low, high):
    return (high << 4) | low


def decode_temp(packet):
    """0xEA4C THN132N and similar: temperature.

    >>> decode(dict(model=0xEA4C, data=0x103A40213003))["data"]
    {'temp': 21.4}
    """
    n = _nibbles(packet["data"], 12)
    # 0 checksum high, 1 ?, 2 flags (bit 3 sign, bits 0-1 hundreds),
    # 3 checksum low, 4-5 temp tens/units, 6 ?, 7 temp tenths,
    # 8-9 rolling code, 10-11 channel
    checksum = (_id_sum(packet["model"]) + n[2] + sum(n[4:12]) - 0xA) & 0xFF
    if checksum != (n[0] << 4) | n[3]:
        raise ValueError("Oregon checksum mismatch")
    temperature = _bcd(n[2] & 0x3, n[5], n[4], n[7]) / 10.0
    return dict(
        packet,
        sensorId=_rolling(n[8], n[9]),
        data=dict(temp=-temperature if n[2] & 0x8 else temperature),
    )


def decode_temp_humidity_v3(packet):
    """0xF824 / 0xF8B4 THGR810, THGN801, 0x1D20 THGN123N: temperature, humidity.

    0x1D20 is not in Telldus' decoder; fw17 frames it like 0xF824 (7 bytes).

    >>> p = decode(dict(model=0xF824, data=0x13B05220640F37))
    >>> p["data"], p["sensorId"]
    ({'temp': 22.5, 'humidity': 46.0}, 59)
    >>> decode(dict(model=0x1D20, data=0x13B01408640637))["data"]
    {'temp': -4.1, 'humidity': 46.0}
    >>> decode(dict(model=0xF824, data=0x13B05220640F47))
    Traceback (most recent call last):
    ...
    ValueError: Oregon checksum mismatch
    """
    n = _nibbles(packet["data"], 14)
    # 0 CRC, 1-2 checksum, 3 ?, 4-5 humidity, 6 sign, 7-9 temp, 10 battery,
    # 11-12 rolling code, 13 channel
    _check(n[1:], n[3:14], packet["model"])
    temperature = _bcd(n[7], n[8], n[9]) / 10.0
    return dict(
        packet,
        sensorId=_rolling(n[11], n[12]),
        data=dict(
            temp=-temperature if n[6] else temperature,
            humidity=float(_bcd(n[4], n[5])),
        ),
    )


def decode_temp_v3(packet):
    """0xC844 THWR800 pool thermometer, 0xEC40 THN132N v3: temperature.

    >>> decode(dict(model=0xEC40, data=0x2A50271093))["data"]
    {'temp': 17.2}
    """
    n = _nibbles(packet["data"], 10)
    # 0-1 checksum, 2 sign, 3-5 temp, 6 battery, 7-8 rolling code, 9 channel
    _check(n, n[2:10], packet["model"])
    temperature = _bcd(n[3], n[4], n[5]) / 10.0
    return dict(
        packet,
        sensorId=_rolling(n[7], n[8]),
        data=dict(temp=-temperature if n[2] else temperature),
    )


def decode_wind(packet):
    """0x1984 / 0x1994 WGR800: wind average and gust (m/s), direction.

    >>> decode(dict(model=0x1984, data=0x17C0400230020533))["data"]
    {'winddirection': 90.0, 'windaverage': 2.0, 'windgust': 3.2}
    """
    n = _nibbles(packet["data"], 16)
    # 0 CRC, 1-2 checksum, 3-5 average, 6-8 gust, 9-10 ?, 11 direction,
    # 12 battery, 13-14 rolling code, 15 channel
    _check(n[1:], n[3:16], packet["model"])
    return dict(
        packet,
        sensorId=_rolling(n[13], n[14]),
        data=dict(
            winddirection=22.5 * n[11],
            windaverage=_bcd(n[3], n[4], n[5]) / 10.0,
            windgust=_bcd(n[6], n[7], n[8]) / 10.0,
        ),
    )


def decode_rain(packet):
    """0x2914 PCR800: rain total (mm) and rate (mm/h).

    >>> decode(dict(model=0x2914, data=0x1E105200102100B2))["data"]
    {'raintotal': 30.5, 'rainrate': 6.3}
    """
    n = _nibbles(packet["data"], 16)
    # 0-1 checksum, 2-7 total (1/1000 inch), 8-11 rate (1/100 inch/h),
    # 12 battery, 13-14 rolling code, 15 channel
    _check(n, n[2:16], packet["model"])
    total = _bcd(*n[2:8]) / 1000.0 * 25.4
    rate = _bcd(*n[8:12]) / 100.0 * 25.4
    return dict(
        packet,
        sensorId=_rolling(n[13], n[14]),
        data=dict(raintotal=round(total, 1), rainrate=round(rate, 1)),
    )


def decode(packet):
    """
    https://raw.githubusercontent.com/telldus/telldus/master/telldus-core/service/ProtocolOregon.cpp

    >>> decode(dict(data=0x201F242450443BDD, model=6701))["data"]["temp"]
    24.2
    >>> decode(dict(data=0x201F242450443BDD, model=6701))["data"]["humidity"]
    45.0
    """

    decoder = DECODERS.get(packet["model"])
    if decoder is not None:
        return decoder(packet)
    if packet["model"] != 6701:
        raise NotImplementedError(
            "The Oregon model %i is not implemented." % packet["model"]
        )

    data = packet["data"]
    value = int(data)
    value >>= 8
    checksum1 = value & 0xFF
    value >>= 8

    checksum = ((value >> 4) & 0xF) + (value & 0xF)
    hum1 = value & 0xF
    value >>= 8

    checksum += ((value >> 4) & 0xF) + (value & 0xF)
    neg = value & (1 << 3)
    hum2 = (value >> 4) & 0xF
    value >>= 8

    checksum += ((value >> 4) & 0xF) + (value & 0xF)
    temp2 = value & 0xF
    temp1 = (value >> 4) & 0xF
    value >>= 8

    checksum += ((value >> 4) & 0xF) + (value & 0xF)
    temp3 = (value >> 4) & 0xF
    value >>= 8

    checksum += ((value >> 4) & 0xF) + (value & 0xF)
    address = value & 0xFF
    value >>= 8

    checksum += ((value >> 4) & 0xF) + (value & 0xF)
    checksum += 0x1 + 0xA + 0x2 + 0xD - 0xA

    if checksum != checksum1:
        raise ValueError(
            "The checksum in the Oregon packet does not match "
            "the caluclated one!"
        )

    temperature = ((temp1 * 100) + (temp2 * 10) + temp3) / 10.0
    if neg:
        temperature = -temperature

    humidity = (hum1 * 10.0) + hum2

    return dict(
        packet,
        sensorId=address,
        data=dict(temp=temperature, humidity=humidity),
    )


DECODERS = {
    0xEA4C: decode_temp,
    0xF824: decode_temp_humidity_v3,
    0xF8B4: decode_temp_humidity_v3,
    0x1D20: decode_temp_humidity_v3,
    0xC844: decode_temp_v3,
    0xEC40: decode_temp_v3,
    0x1984: decode_wind,
    0x1994: decode_wind,
    0x2914: decode_rain,
}
