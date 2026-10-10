"""実機マップの番号を、撮影して確認した4x5pxの数字で読む。"""
DIGITS = {'1': '11100110011001101111', '2': '11100011111110001111', '3': '11100011111100111110', '4': '10111011111100110011', '5': '11111000111100111110', '6': '11111000111110111111', '7': '11110011011011001100', '8': '11111011111110111111', '9': '11111011111100110011', '0': '01101011101110110110'}

def level_number(img):
    def digit(x):
        # 選択直後の橙の数字も、通常の白の数字も同じ字形。
        bits = ''.join(str(int(img.get(xx, y)[0] > 220 and img.get(xx, y)[1] > 100))
                       for y in range(14, 19) for xx in range(x, x + 4))
        return next((int(k) for k, value in DIGITS.items() if value == bits), None)
    tens, ones = digit(27), digit(32)
    if tens is None or ones is None:
        return None
    level = tens * 10 + ones
    return level if 1 <= level <= 50 else None
