"""开盘挂单的「排队版」成交算法（2026-11-05 加，开盘复盘.py 的记分卡和两年回放脚本都用）。

为什么要它：老口径（开盘复盘.simulate、两年回放的 fills）是「那半秒两腿成交均价差或立刻能成交的价比挂价好就成交，按更好的价算」，
等于全市场的量都是自己的、永远排第一，是上限。11-05 早 bu2702-2701：首笔半 -9，那半秒均价价差 1.75，老口径判成交在 1.75；
可实际老陆挂 -42 成交在 +20 左右，另有人挂 -31 整场没成交——开盘那一下是价格优先、再时间优先，挂得近的先吃，后面的轮不到。

他们程序的做法（11-05 老陆的委托单 13 单核对过，见 测试/2026-11-05_排队回测/老陆委托对照.py）：
  开盘那一秒（21:00:00 / 09:00:00）才下腿1，价 = 挂价差 + 腿2 竞价后那张快照的对手价（卖价差看腿2 卖一、买价差看腿2 买一）；
  腿1 成交后腿2 按对手价 ± 1 档马上追（7 次里 6 次追在竞价后的价附近，br 那次腿2 跑了亏 27 档）；
  没成交的撤掉，约 1 秒后按腿2 新的对手价重下（跟价，follow=True 近似这个）。先下的都是腿1（hung 参数留着备用）。
  下面模型里腿1 挂价 P1 = 挂价差 L + ref2（调用方传腿2 竞价后对手价），取整到一档；
  腿2 都按腿1 成交那张的下一张快照的对手价追（比实际偏保守）。

  一、「只接开盘那一下」（first_only=True）：只看开盘后第一张快照（开盘撮合那一下）。
      卖价差（s = +1，卖腿1）：这半秒腿1 有成交、而且半秒末腿1 卖一 ≥ P1（撮合停下的地方没比 P1 便宜，P1 及更便宜的卖单都轮到了；
      正好相等算接到）才成交；成交价按这半秒腿1 成交均价（排在前面的吃到的是挂着的高买价），比 P1 差就按 P1。买价差反过来。
      第一张没接到就算没接到。11-05 bu：-42（腿1 4378，半秒末卖一 4378）接到、-31（4389）和 -9（4411）接不到，和实际一致。
  11-05 夜按审查（测试/2026-11-05_成交模型审查/审查结果.md）改：
  - 成交要有证据：挂着的卖单只有「这张快照腿1 买一 ≥ 挂价」或「这半秒腿1 成交均价 > 挂价」（有人在比我们贵的价成交了，价格优先该先吃我们）才算成交；
    卖一往上移不算（可能是别人撤单，我们的单还挂着）。买单反过来。
  - 腿2 按「腿1 成交后 0.5 秒那张」的对手价追，按时间找、不按数组下一行；那张没有（数据缺）就算没成交，不退回用当前价。
  - 「只接开盘那一下」只认开盘后 0.5 秒那张；first_px="limit" 是保守版：开盘那一下的成交价按自己的挂价、不给均价。
  二、「排队 5 秒」（first_only=False）：开盘后 valid 秒内，某张快照里腿1 卖一 > P1（或买一 > P1）就算轮到、按 P1 成交（不给正滑）；
      卖一 = P1 算还在排队。买价差反过来。11-05 bu 这版会判 -31 在 1.0 秒成交（腿1 弹回 4432），和实际不符，
      可能是程序开盘没接到就改价或撤单——等委托记录对过再定哪版对（见 分析/2026-11-05/排队回测/小结.md）。
"""
import math

EPS = 1e-9


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def leg2_ref(auction=None, auction_vol=None, bid=None, ask=None, prev=None):
    """腿2 参考价：竞价成交了用竞价价，否则竞价后盘口中间价，再否则昨收。"""
    a, v, b, k, p = (_num(x) for x in (auction, auction_vol, bid, ask, prev))
    if a is not None and (v is None or v > 0):
        return a
    if b is not None and k is not None:
        return (b + k) / 2
    return p


def queue_fill(books, s, L, ref2, tick, valid=5.0, first_only=False, follow=False, hung=1, start=None, first_px="avg", delay=0.5):
    """books：开盘后每张快照 [(秒, 腿1买一, 腿1卖一, 腿2买一, 腿2卖一, 腿1这半秒成交均价, 腿2这半秒成交均价), ...]（均价没成交写 None），
    按时间排；s = +1 卖价差、-1 买价差（价差 = 腿1 − 腿2）。ref2 = 追的那条腿的参考价。
    hung = 挂着排队的是哪条腿（委托单看都是腿1，2 留着备用）。
    返回 None（没接到）或 {"腿1价", "腿1秒", "腿2价", "成交秒", "成交价差"}，成交秒 = 腿2 追到的那张（腿1 成交后 delay 秒）。
    follow=True：挂价跟着腿2 改 = L + 腿2 这张的对手价（程序没成交会撤单重挂）。新挂的单是看到这张快照才下的，下一张才看成没成，按挂价、不给正滑。
    start：开盘后几秒才第一次下单（程序版 = 0.5：开盘那一下没接到，撤单后重挂），之前的快照不看。
    first_px：「一下」的成交价，"avg" = max(挂价, 腿1 那半秒均价)（偏乐观），"limit" = 挂价（保守），"half" = 正滑拿一半。
      11-05 委托 12 单开盘那一秒的成交：真实正滑合计约是半秒均价算的 60%（针大时 4–50%、针小时 100% 上下）。"""
    if hung == 2:  # 换成「腿2 − 腿1」这个价差来算：方向、挂价、结果都反过来
        sw = [(b[0], b[3], b[4], b[1], b[2], *(list(b[5:7])[::-1] if len(b) >= 7 else [None])) for b in books]
        r = queue_fill(sw, -s, -_num(L) if _num(L) is not None else None, ref2, tick, valid, first_only, follow, 1, start, first_px, delay)
        return r and r | {"成交价差": -r["成交价差"]}
    ref2, L, tick = _num(ref2), _num(L), _num(tick)
    if ref2 is None or L is None or not tick:
        return None
    p1 = round(round((L + ref2) / tick) * tick, 6)
    resting = p1 if start is None else None  # 正挂着的单（开盘那一秒下的；start 时还没下）

    def filled(price, b1, avg):  # 有证据说明挂在 price 的单成交了
        if s > 0:
            return (b1 is not None and b1 >= price - EPS) or (avg is not None and avg > price + EPS)
        return (b1 is not None and b1 <= price + EPS) or (avg is not None and avg < price - EPS)

    for i, (t, b1, a1, b2, a2, *rest) in enumerate(books):
        if t > valid + EPS or (first_only and i > 0):
            break
        if start is not None and t <= start + EPS:
            continue
        b1, a1 = _num(b1), _num(a1)
        avg = _num(rest[0]) if rest else None
        if follow and not first_only:
            if not (resting is not None and filled(resting, b1 if s > 0 else a1, avg)):
                r2 = _num(a2 if s > 0 else b2)
                if r2 is not None:
                    resting = round(round((L + r2) / tick) * tick, 6)  # 撤掉，按腿2 这张的对手价重挂，下一张再看
                continue
            px1 = resting
        elif first_only:
            if t > 0.5 + 0.05 or avg is None:  # 只认开盘后 0.5 秒那张，腿1 这半秒要有成交
                break
            edge = a1 if s > 0 else b1  # 撮合停下的地方：卖价差看剩下的卖一，买价差看剩下的买一
            if edge is None:
                edge = b1 if s > 0 else a1
            if edge is None or s * (edge - p1) < -EPS:
                break
            k = {"avg": 1.0, "half": 0.5, "limit": 0.0}.get(first_px, first_px)  # 拿到半秒均价那份正滑的几成
            best = max(p1, avg) if s > 0 else min(p1, avg)
            px1 = round(p1 + k * (best - p1), 6)
        else:
            if not filled(p1, b1 if s > 0 else a1, avg):
                continue
            px1 = p1
        for j in range(i + 1, len(books)):  # 腿2：腿1 成交后 delay 秒那张的对手价；那张缺就算没成交
            tj = books[j][0]
            if tj > t + delay + 0.05:
                break
            if abs(tj - (t + delay)) <= 0.05:
                p2 = _num(books[j][4] if s > 0 else books[j][3])
                if p2 is not None:
                    return {"腿1价": round(px1, 6), "腿1秒": t, "腿2价": p2, "成交秒": tj, "成交价差": round(px1 - p2, 6)}
                break
        return None
    return None
