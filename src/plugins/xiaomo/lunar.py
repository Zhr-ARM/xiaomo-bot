"""农历 + 黄历（宜忌）模块

农历数据基于 zhdate 库，宜忌基于建除十二神推算。
"""

from datetime import date, datetime, timedelta
from zhdate import ZhDate

# ── 天干地支 ──────────────────────────────────────────────────

TIAN_GAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
DI_ZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
SHENG_XIAO = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]

JIAN_CHU_NAMES = ["建", "除", "满", "平", "定", "执", "破", "危", "成", "收", "开", "闭"]

# ── 建除十二神宜忌 ────────────────────────────────────────────

_JIAN_CHU_YIJI: dict[str, tuple[str, str]] = {
    "建": ("出行 会友 订婚 交易 入学", "动土 开仓 安葬"),
    "除": ("除服 疗病 扫舍 祭祀", "求官 上任 嫁娶"),
    "满": ("祭祀 祈福 开市 纳财 订婚", "动土 搬家 安葬 栽种"),
    "平": ("出行 订婚 装修 交易 修饰", "栽种 安葬 诉讼"),
    "定": ("订婚 交易 入学 纳财", "诉讼 出行 安葬"),
    "执": ("捕捉 畋猎 修造", "开市 交易 搬家 出行"),
    "破": ("破屋 求医 拆卸", "诸事不宜"),
    "危": ("安床 祭祀 订婚 纳畜", "出行 开市 动土"),
    "成": ("嫁娶 开市 出行 入学 上任 签约", "诉讼"),
    "收": ("纳财 捕捉 入学 祭祀", "安葬 出行 搬家"),
    "开": ("嫁娶 开市 出行 上任 交易 会友", "安葬"),
    "闭": ("安葬 修坟 祭祀 祈福", "开市 出行 嫁娶 动土"),
}


def _ganzhi_day(dt: date) -> tuple[str, str]:
    """干支纪日。参考点: 1900-01-01 = 甲戌日（干支序号 10）"""
    days = (dt - date(1900, 1, 1)).days
    idx = (days + 10) % 60  # 0-59: 甲子=0 ... 癸亥=59
    return TIAN_GAN[idx % 10], DI_ZHI[idx % 12]


def _month_zhi(dt: date) -> str:
    """月建（月地支），由节气决定。
    立春(~2/4)起寅月，惊蛰(~3/6)起卯月，… 逐月顺推。
    这里取近似节气日，误差在 ±1 天以内，对宜忌无实质影响。
    """
    y = dt.year
    terms = [
        (date(y, 2, 4),  "寅"),
        (date(y, 3, 6),  "卯"),
        (date(y, 4, 5),  "辰"),
        (date(y, 5, 6),  "巳"),
        (date(y, 6, 6),  "午"),
        (date(y, 7, 7),  "未"),
        (date(y, 8, 8),  "申"),
        (date(y, 9, 8),  "酉"),
        (date(y, 10, 8), "戌"),
        (date(y, 11, 7), "亥"),
        (date(y, 12, 7), "子"),
        (date(y, 1, 6),  "丑"),  # 次年小寒
    ]
    for i, (term_date, zhi) in enumerate(terms):
        if i < 11:
            next_term = terms[i + 1][0]
        else:
            next_term = date(y + 1, 2, 4)
        if term_date <= dt < next_term:
            return zhi
    return "子"


def _jianchu(dt: date) -> str:
    """建除十二神"""
    day_zhi = _ganzhi_day(dt)[1]
    month_zhi = _month_zhi(dt)
    day_idx = DI_ZHI.index(day_zhi)
    month_idx = DI_ZHI.index(month_zhi)
    delta = (day_idx - month_idx) % 12
    return JIAN_CHU_NAMES[delta]


# ── 公开接口 ──────────────────────────────────────────────────


def get_day_ganzhi(dt: date | None = None) -> str:
    """返回日干支，如 '甲子'"""
    if dt is None:
        dt = date.today()
    g, z = _ganzhi_day(dt)
    return f"{g}{z}"


def get_almanac(dt: date | None = None) -> dict:
    """返回当日黄历信息"""
    if dt is None:
        dt = date.today()

    lunar = ZhDate.from_datetime(datetime.combine(dt, datetime.min.time()))
    jianchu = _jianchu(dt)
    yi, ji = _JIAN_CHU_YIJI[jianchu]
    day_gan, day_zhi = _ganzhi_day(dt)
    day_ganzhi = f"{day_gan}{day_zhi}"

    # 农历月日带闰月标注
    leap_tag = "闰" if lunar.leap_month else ""
    lunar_str = f"{leap_tag}{lunar.lunar_month}月{lunar.lunar_day}日"

    # 生肖
    zodiac = SHENG_XIAO[(lunar.lunar_year - 4) % 12]

    return {
        "lunar_str": lunar_str,
        "lunar_year": lunar.lunar_year,
        "zodiac": zodiac,
        "day_ganzhi": day_ganzhi,
        "jianchu": jianchu,
        "yi": yi,
        "ji": ji,
    }


def format_almanac(dt: date | None = None) -> str:
    """格式化为系统提示词片段"""
    info = get_almanac(dt)
    return (
        f"## 黄历\n"
        f"- 农历：{info['lunar_year']}年（{info['zodiac']}年）{info['lunar_str']}\n"
        f"- 日干支：{info['day_ganzhi']}  建除十二神：{info['jianchu']}\n"
        f"- 宜：{info['yi']}\n"
        f"- 忌：{info['ji']}"
    )
