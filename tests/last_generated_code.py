```python
# ШАГ 1: Основание со скругленным ухом R32 и отверстием ф30
sk_base = comp_def.Sketches.Add(wp.Item(3)) # XY
draw_flange(sk_base, 0, 0, radius=mm(32), length=mm(70), direction="+x")
extrude(sk_base, mm(10))

# Центральное сквозное отверстие в ухе строго в центре (0, 0)
sk_hole = comp_def.Sketches.Add(wp.Item(3))
cut_hole(sk_hole, 0, 0, diameter=mm(30), through_all=True)

# ШАГ 2: Вторичные элементы (стойки/ребра)
sk_boss1 = comp_def.Sketches.Add(wp.Item(3))
draw_rectangle(sk_boss1, mm(-45), mm(-15), mm(90), mm(35))
extrude(sk_boss1, mm(20))

sk_boss2 = comp_def.Sketches.Add(wp.Item(3))
draw_rectangle(sk_boss2, mm(45), mm(-15), mm(90), mm(35))
extrude(sk_boss2, mm(20))

# ШАГ 3: Отверстие ф30 в ухе
sk_hole = comp_def.Sketches.Add(wp.Item(3))
cut_hole(sk_hole, 0, 0, diameter=mm(30), through_all=True)

