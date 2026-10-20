# E-ink planner screen head: three enclosure options side by side, each with
# an exploded copy, layers numbered in assembly order.
#
#   Layered ply  - bezel, tape shim, hollow spacer frame, panel, back plate
#   Picture frame - 13x18cm frame (glass removed), card mount, backing board
#   Two plates   - front plate, panel on narrow tape, back plate
#
# In SketchUp: Extensions > Developer > Ruby Console, then
#   load '/Users/oli/Coding/e-ink planner/enclosure/eink_mount.rb'
# Re-running replaces only the mock-up groups. Sizes are mm. Values marked
# TODO(oli) are estimates waiting for a real measurement.
#
# Shared by all three: the panel's ribbons wrap round the glass's bottom edge,
# run up behind it in the 1mm foam-tape gap and leave through holes in the back
# sheet; the FTS02 sits behind on standoffs with P5 just above the display
# hole; a block of four 3mm offcuts takes the arm's 1/4"-20 insert.

require 'sketchup.rb'

module EinkMount
  P = {
    # GDEY075T7-T01 (GoodDisplay datasheet)
    panel_w: 170.2, panel_h: 111.2, panel_t: 2.13,
    active_w: 163.2, active_h: 97.92,
    border_top: 3.49,                     # glass top edge to the display area (drawing)
    # Ribbons leaving the bottom edge, seen from the front: centre x (from panel centre) and width.
    # Display tail: 12.5 wide, right edge 79.9 from the glass's right edge (drawing); pin 1 on the left.
    disp_tail_x: -1.05, disp_tail_w: 12.5,
    # Touch flex where it leaves the glass: not dimensioned, scaled off the drawing
    touch_tail_x: -45.5, touch_tail_w: 28.0,
    fold_r: 1.5,       # ribbons wrap round the glass edge with about this radius
    fold_room: 3.0,    # space below the glass for that fold

    # Foam tape (YoiYee PE foam, 8mm x 1mm)
    tape_t: 1.0, tape_w: 8.0,
    clearance: 0.5,    # panel to any wall
    window_over: 1.0,  # window beyond the display area, so nothing presses the touch edge

    # Cable holes in the back sheet, behind the panel's bottom edge (cut generous, mark by test fit)
    hole_z0: 2.0, disp_hole_h: 12.0, touch_hole_h: 24.0, hole_margin: 4.0,

    # FTS02 on the back, component side facing the back sheet so P5 meets the display ribbon
    board_w: 58.6, board_h: 54.7, board_t: 1.6,
    p5_from_left: 30.0, p7_from_left: 8.0,  # TODO(oli): connector centres from the board's left edge
    standoff: 4.0, socket_h: 8.5, plug_h: 14.0,
    lid_t: 3.0, lid_margin: 4.0,
    block_w: 30.0, block_h: 30.0, block_t: 12.0, block_x: -35.0, block_z: 70.0,
    insert_d: 8.0, insert_len: 12.0,

    # Layered ply: all 3mm A4 birch, back plate two sheets glued
    wall: 6.0, bezel_t: 3.0, shim_t: 1.0, spacer_t: 3.0, back_t: 6.0,

    # Picture frame: a generic 13x18cm (5x7in) frame. TODO(oli): measure the one you buy
    frame_rebate_w: 178.0, frame_rebate_h: 127.0,  # recess the glass sat in
    frame_lip: 5.0,          # how far the front lip overhangs the rebate
    frame_moulding: 15.0,    # frame width outside the rebate
    frame_depth: 15.0, frame_rebate_depth: 8.0,
    mount_t: 1.5, backing_t: 2.5,

    # Two plates: one 3mm front plate and one 3mm back plate
    tp_margin: 3.0,          # plate beyond the panel at sides and top
    tp_front_t: 3.0, tp_back_t: 3.0,

    explode_gap: 45.0, explode_dx: 330.0, variant_dx: 800.0,
  }

  VARIANTS = [
    [:layered, 'Layered'],
    [:frame, 'Picture frame'],
    [:two_plate, 'Two plates'],
  ].freeze

  @summary = ''

  class << self
    attr_reader :summary
  end

  def self.pt(x, y, z)
    Geom::Point3d.new(x.mm, y.mm, z.mm)
  end

  def self.rect(x0, z0, x1, z1)
    [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
  end

  def self.material(model, name, rgb)
    m = model.materials[name] || model.materials.add(name)
    m.color = Sketchup::Color.new(*rgb)
    m
  end

  def self.track(g)
    @parts << [g, @step] if @parts
    g
  end

  # Slab in the XZ plane from y0 to y0 + t; outline and holes are [[x, z], ...].
  def self.slab(parent, name, outline, y0, t, mat, holes = [])
    g = parent.add_group
    g.name = name
    e = g.entities
    e.add_face(outline.map { |x, z| pt(x, y0, z) })
    holes.each do |h|
      inner = e.add_face(h.map { |x, z| pt(x, y0, z) })
      inner.erase! if inner && inner.valid?
    end
    face = e.grep(Sketchup::Face).max_by(&:area)
    face.pushpull(face.normal.y > 0 ? t.mm : -t.mm)
    g.material = mat
    track(g)
  end

  def self.box(parent, name, x0, y0, z0, x1, y1, z1, mat)
    x0, x1 = [x0, x1].minmax
    y0, y1 = [y0, y1].minmax
    z0, z1 = [z0, z1].minmax
    slab(parent, name, rect(x0, z0, x1, z1), y0, y1 - y0, mat)
  end

  def self.rod(parent, name, a, b, r, mat)
    g = parent.add_group
    g.name = name
    pa = pt(*a)
    vec = pa.vector_to(pt(*b))
    e = g.entities
    face = e.add_face(e.add_circle(pa, vec, r.mm, 24))
    face.reverse! if face.normal.dot(vec) < 0
    face.pushpull(vec.length)
    g.material = mat
    track(g)
  end

  def self.materials(model)
    {
      ply: material(model, 'Plywood', [222, 190, 140]),
      ply2: material(model, 'Plywood (spacer)', [196, 160, 110]),
      frame: material(model, 'Picture frame', [45, 45, 45]),
      mount: material(model, 'Mount card', [240, 236, 225]),
      mdf: material(model, 'Frame backing board', [150, 120, 90]),
      glass: material(model, 'Panel glass', [35, 35, 35]),
      paper: material(model, 'E-paper', [228, 226, 218]),
      tape: material(model, 'Foam tape', [250, 250, 250]),
      flex: material(model, 'Ribbon', [205, 135, 40]),
      ffc: material(model, 'White ribbon', [240, 240, 245]),
      chip: material(model, 'Touch chip', [20, 20, 20]),
      pcb: material(model, 'FTS02 board', [25, 80, 160]),
      yellow: material(model, 'Touch extension board', [200, 190, 60]),
      conn: material(model, 'Connectors', [235, 235, 228]),
      wires: material(model, 'Jumper bundle', [120, 100, 200]),
      metal: material(model, 'Metal', [60, 60, 60]),
      knob: material(model, 'Arm knobs', [230, 110, 30]),
      brass: material(model, 'Insert', [190, 160, 70]),
      shelf: material(model, 'Shelf', [120, 75, 45]),
    }
  end

  # Panel-relative geometry shared by every variant: panel bottom edge at z = 0,
  # centred on x = 0.
  def self.basics
    p = P
    aw = p[:active_w] / 2.0
    az1 = p[:panel_h] - p[:border_top]
    az0 = az1 - p[:active_h]
    wo = p[:window_over]
    hm = p[:hole_margin]
    holes = {
      disp: [p[:disp_tail_x], p[:disp_tail_w], p[:disp_hole_h]],
      touch: [p[:touch_tail_x], p[:touch_tail_w], p[:touch_hole_h]],
    }
    holes = Hash[holes.map { |k, (x, w, h)| [k, [x - w / 2.0 - hm, p[:hole_z0], x + w / 2.0 + hm, p[:hole_z0] + h]] }]
    {
      hw: p[:panel_w] / 2.0, aw: aw, az0: az0, az1: az1,
      win: [-aw - wo, az0 - wo, aw + wo, az1 + wo],
      holes: holes,
      disp_zc: p[:hole_z0] + p[:disp_hole_h] / 2.0,
      touch_zc: p[:hole_z0] + 6.0,
    }
  end

  # Where the board, lid and block sit for a back sheet whose rear face is at y_rear.
  def self.rear_geom(y_rear)
    p = P
    b = basics
    bx0 = p[:disp_tail_x] - p[:p5_from_left]
    bz0 = b[:disp_zc] + 4.0
    by0 = y_rear + p[:standoff]
    by1 = by0 + p[:board_t]
    plug_y1 = by1 + p[:socket_h] + p[:plug_h]
    lid_y = plug_y1 + 2
    {
      bx0: bx0, bx1: bx0 + p[:board_w], bz0: bz0, bz1: bz0 + p[:board_h], by0: by0, by1: by1,
      plug_y1: plug_y1, lid_y: lid_y, lid_back: lid_y + p[:lid_t], p7x: bx0 + p[:p7_from_left],
      block: [p[:block_x], y_rear + p[:block_t], p[:block_z]],
    }
  end

  # A ribbon wrapping round the glass's bottom edge, up behind the panel, out
  # through its hole and (optionally) up into a connector at up_to_z.
  def self.ribbon(e, name, x, w, py0, py1, out_y, out_z, up_to_z, mat)
    x0 = x - w / 2.0
    x1 = x + w / 2.0
    r = P[:fold_r]
    ft = 0.2
    box(e, "#{name}: off the glass", x0, py0 + 1.0, -r, x1, py0 + 1.0 + ft, 0, mat)
    box(e, "#{name}: fold", x0, py0 + 1.0, -r - ft, x1, py1 + 0.3, -r, mat)
    box(e, "#{name}: up behind panel", x0, py1 + 0.1, -r, x1, py1 + 0.3, out_z, mat)
    box(e, "#{name}: through hole", x0, py1 + 0.1, out_z - ft, x1, out_y, out_z, mat)
    box(e, "#{name}: into connector", x0, out_y - ft, out_z, x1, out_y, up_to_z, mat) if up_to_z && up_to_z > out_z
  end

  def self.panel(e, m, py0, y_rear)
    p = P
    b = basics
    r = rear_geom(y_rear)
    py1 = py0 + p[:panel_t]
    box(e, 'Panel (glass + touch)', -b[:hw], py0, 0, b[:hw], py1, p[:panel_h], m[:glass])
    box(e, 'Display area', -b[:aw], py0 - 0.15, b[:az0], b[:aw], py0 - 0.05, b[:az1], m[:paper])
    ribbon(e, 'Display ribbon', p[:disp_tail_x], p[:disp_tail_w], py0, py1, r[:by0] - 1.25, b[:disp_zc], r[:bz0] + 2, m[:flex])
    ribbon(e, 'Touch flex', p[:touch_tail_x], p[:touch_tail_w], py0, py1, y_rear + 0.6, b[:touch_zc], nil, m[:flex])
    box(e, 'Touch chip', p[:touch_tail_x] - 4, py1 + 0.3, b[:touch_zc] + 3, p[:touch_tail_x] + 4, py1 + 1.3,
        b[:touch_zc] + 11, m[:chip])
  end

  # Foam tape on the panel's back, with breaks where the ribbons run up behind it.
  def self.back_tape(e, m, py1)
    p = P
    b = basics
    hw = b[:hw]
    tw = p[:tape_w]
    gaps = b[:holes].values.map { |x0, _z0, x1, _z1| [x0 - 1, x1 + 1] }.sort
    bottom = []
    x = -hw
    gaps.each do |a, c|
      bottom << [x, a] if a > x
      x = [x, c].max
    end
    bottom << [x, hw] if hw > x
    strips = [rect(-hw, p[:panel_h] - tw, hw, p[:panel_h]),
              rect(-hw, tw, -hw + tw, p[:panel_h] - tw), rect(hw - tw, tw, hw, p[:panel_h] - tw)]
    strips += bottom.select { |a, c| c - a > tw }.map { |a, c| rect(a, 0, c, tw) }
    strips.each_with_index { |rr, i| slab(e, "Foam tape #{i + 1}", rr, py1, p[:tape_t], m[:tape]) }
  end

  def self.board_and_block(e, m, y_rear)
    p = P
    b = basics
    r = rear_geom(y_rear)
    bx0, bx1, bz0, bz1, by0, by1 = r.values_at(:bx0, :bx1, :bz0, :bz1, :by0, :by1)
    tz = b[:touch_zc]
    box(e, 'FTS02 board', bx0, by0, bz0, bx1, by1, bz1, m[:pcb])
    box(e, 'P5 display connector', p[:disp_tail_x] - 9, by0 - 2.5, bz0, p[:disp_tail_x] + 9, by0, bz0 + 5, m[:conn])
    box(e, 'P7 touch connector', r[:p7x] - 4, by0 - 2.5, bz0, r[:p7x] + 4, by0, bz0 + 5, m[:conn])
    [[bx0 + 3, bz1 - 3], [bx1 - 3, bz1 - 3], [bx1 - 3, bz0 + 8], [bx0 + 14, bz0 + 8]].each_with_index do |(sx, sz), i|
      rod(e, "Standoff #{i + 1}", [sx, y_rear, sz], [sx, by0, sz], 2.5, m[:metal])
    end
    sx0 = (bx0 + bx1) / 2.0 - 25.5
    box(e, 'P1 40-pin socket', sx0, by1, bz1 - 6, sx0 + 51, by1 + p[:socket_h], bz1 - 1, m[:metal])
    box(e, 'Jumper plugs', sx0 + 4, by1 + p[:socket_h], bz1 - 6, sx0 + 47, r[:plug_y1], bz1 - 1, m[:wires])
    ext_x0 = b[:holes][:touch][2] + 3
    ext_x1 = ext_x0 + 22
    box(e, 'Touch extension board', ext_x0, y_rear, tz - 6, ext_x1, y_rear + 1.6, tz + 6, m[:yellow])
    box(e, 'Touch flex: to extension', b[:holes][:touch][2] - 4, y_rear + 0.4, tz - 3, ext_x0, y_rear + 0.6, tz + 3, m[:flex])
    box(e, 'White touch ribbon: along', ext_x1, y_rear + 0.4, tz - 2, r[:p7x] + 2, y_rear + 0.6, tz + 2, m[:ffc])
    box(e, 'White touch ribbon: into P7', r[:p7x] - 2, by0 - 1.4, tz - 2, r[:p7x] + 2, by0 - 1.2, bz0 + 2, m[:ffc])
    bx, block_back, bz = r[:block]
    box(e, 'Arm block (4 x 3mm offcuts)', bx - p[:block_w] / 2.0, y_rear, bz - p[:block_h] / 2.0,
        bx + p[:block_w] / 2.0, block_back, bz + p[:block_h] / 2.0, m[:ply2])
    rod(e, '1/4"-20 insert', [bx, block_back - p[:insert_len], bz], [bx, block_back + 0.2, bz], p[:insert_d] / 2.0, m[:brass])
  end

  # Lid on posts over the board; returns where the wire bundle leaves.
  def self.lid(e, m, y_rear, top_z)
    p = P
    r = rear_geom(y_rear)
    bx0, bx1, bz0, bz1 = r.values_at(:bx0, :bx1, :bz0, :bz1)
    lm = p[:lid_margin]
    box(e, 'Lid (3mm ply offcut)', bx0 - lm, r[:lid_y], bz0 - lm, bx1 + lm, r[:lid_back], bz1 + lm, m[:ply])
    [[bx0 - lm + 3, bz0 - lm + 3], [bx1 + lm - 3, bz0 - lm + 3], [bx0 - lm + 3, bz1 + lm - 3], [bx1 + lm - 3, bz1 + lm - 3]]
      .each_with_index { |(lx, lz), i| rod(e, "Lid post #{i + 1}", [lx, y_rear, lz], [lx, r[:lid_y], lz], 3, m[:metal]) }
    wx = (bx0 + bx1) / 2.0
    wy = (r[:by1] + p[:socket_h] + r[:plug_y1]) / 2.0
    rod(e, 'Wire bundle (out of lid)', [wx, wy, bz1 - 4], [wx, wy, top_z + 20], 4, m[:wires])
    [wx, wy, top_z + 20]
  end

  # --- Variants. Each returns the outer extents, the visible window, and a
  # front-face depth for each assembly step (for the exploded labels).

  def self.layered(e, m)
    p = P
    b = basics
    c = p[:clearance]
    hw = b[:hw]
    fx = hw + c + p[:wall]
    fz0 = -(c + p[:fold_room] + p[:wall])
    fz1 = p[:panel_h] + c + p[:wall]
    outline = rect(-fx, fz0, fx, fz1)
    frame_hole = rect(-hw - c, -c - p[:fold_room], hw + c, p[:panel_h] + c)
    y_spacer = p[:bezel_t] + p[:shim_t]
    y_back = y_spacer + p[:spacer_t]
    y_rear = y_back + p[:back_t]
    py1 = y_back - p[:tape_t]
    py0 = py1 - p[:panel_t]

    @step = 1
    slab(e, 'Bezel (3mm ply)', outline, 0, p[:bezel_t], m[:ply], [rect(*b[:win])])
    @step = 2
    slab(e, 'Shim (foam tape)', outline, p[:bezel_t], p[:shim_t], m[:tape], [frame_hole])
    @step = 3
    slab(e, 'Spacer frame (3mm ply, hollow)', outline, y_spacer, p[:spacer_t], m[:ply2], [frame_hole])
    @step = 4
    panel(e, m, py0, y_rear)
    @step = 5
    back_tape(e, m, py1)
    @step = 6
    slab(e, 'Back plate (2 x 3mm ply)', outline, y_back, p[:back_t], m[:ply], b[:holes].values.map { |h| rect(*h) })
    @step = 7
    board_and_block(e, m, y_rear)
    @step = 8
    wire_top = lid(e, m, y_rear, fz1)
    {
      x0: -fx, x1: fx, z0: fz0, z1: fz1, gap: py0 - p[:bezel_t], depth: rear_geom(y_rear)[:lid_back],
      wood_depth: y_rear, wire_top: wire_top, block: rear_geom(y_rear)[:block],
      steps: { 1 => [0, '1 Bezel'], 2 => [p[:bezel_t], '2 Tape shim'], 3 => [y_spacer, '3 Spacer frame'],
               4 => [py0, '4 Panel + ribbons'], 5 => [py1, '5 Foam tape'], 6 => [y_back, '6 Back plate'],
               7 => [y_rear, '7 Board + arm block'], 8 => [rear_geom(y_rear)[:lid_y], '8 Lid'] },
    }
  end

  def self.frame(e, m)
    p = P
    b = basics
    rx = p[:frame_rebate_w] / 2.0
    rz0 = -(p[:fold_room] + 1.0)
    rz1 = rz0 + p[:frame_rebate_h]
    lip = p[:frame_lip]
    mw = p[:frame_moulding]
    ox = rx + mw
    oz0 = rz0 - mw
    oz1 = rz1 + mw
    outline = rect(-ox, oz0, ox, oz1)
    rebate = rect(-rx, rz0, rx, rz1)
    lip_t = p[:frame_depth] - p[:frame_rebate_depth]
    py0 = lip_t + p[:mount_t]
    py1 = py0 + p[:panel_t]
    y_back = py1 + p[:tape_t]
    y_rear = y_back + p[:backing_t]

    @step = 1
    slab(e, 'Frame: front lip (glass removed)', outline, 0, lip_t, m[:frame], [rect(-rx + lip, rz0 + lip, rx - lip, rz1 - lip)])
    slab(e, 'Frame: rebate walls', outline, lip_t, p[:frame_rebate_depth], m[:frame], [rebate])
    @step = 2
    slab(e, 'Mount card (window = display area)', rebate, lip_t, p[:mount_t], m[:mount], [rect(*b[:win])])
    @step = 3
    panel(e, m, py0, y_rear)
    @step = 4
    back_tape(e, m, py1)
    @step = 5
    slab(e, 'Backing board (cable holes)', rebate, y_back, p[:backing_t], m[:mdf], b[:holes].values.map { |h| rect(*h) })
    @step = 6
    board_and_block(e, m, y_rear)
    @step = 7
    wire_top = lid(e, m, y_rear, oz1)
    {
      x0: -ox, x1: ox, z0: oz0, z1: oz1, gap: 0.0, depth: rear_geom(y_rear)[:lid_back],
      wood_depth: [y_rear, p[:frame_depth]].max, wire_top: wire_top, block: rear_geom(y_rear)[:block],
      rebate_spare: p[:frame_depth] - y_rear,
      steps: { 1 => [0, '1 Frame (no glass)'], 2 => [lip_t, '2 Mount card'], 3 => [py0, '3 Panel + ribbons'],
               4 => [py1, '4 Foam tape'], 5 => [y_back, '5 Backing board'], 6 => [y_rear, '6 Board + arm block'],
               7 => [rear_geom(y_rear)[:lid_y], '7 Lid'] },
    }
  end

  def self.two_plate(e, m)
    p = P
    b = basics
    hw = b[:hw]
    win = b[:win]
    mg = p[:tp_margin]
    fx = hw + mg
    fz0 = -(p[:fold_room] + mg)
    fz1 = p[:panel_h] + mg
    outline = rect(-fx, fz0, fx, fz1)
    py0 = p[:tp_front_t] + p[:tape_t]
    py1 = py0 + p[:panel_t]
    y_back = py1 + p[:tape_t]
    y_rear = y_back + p[:tp_back_t]

    @step = 1
    slab(e, 'Front plate (3mm ply)', outline, 0, p[:tp_front_t], m[:ply], [rect(*win)])
    @step = 2
    [rect(-hw, win[1], win[0], win[3]), rect(win[2], win[1], hw, win[3]),
     rect(-hw, win[3], hw, p[:panel_h]), rect(-hw, 0, hw, win[1])].each_with_index do |rr, i|
      slab(e, "Front tape #{i + 1} (cut to fit the border)", rr, p[:tp_front_t], p[:tape_t], m[:tape])
    end
    @step = 3
    panel(e, m, py0, y_rear)
    @step = 4
    back_tape(e, m, py1)
    @step = 5
    slab(e, 'Back plate (3mm ply)', outline, y_back, p[:tp_back_t], m[:ply], b[:holes].values.map { |h| rect(*h) })
    @step = 6
    board_and_block(e, m, y_rear)
    @step = 7
    wire_top = lid(e, m, y_rear, fz1)
    {
      x0: -fx, x1: fx, z0: fz0, z1: fz1, gap: 0.0, depth: rear_geom(y_rear)[:lid_back],
      wood_depth: y_rear, wire_top: wire_top, block: rear_geom(y_rear)[:block],
      side_tape_w: hw - win[2],
      steps: { 1 => [0, '1 Front plate'], 2 => [p[:tp_front_t], '2 Front tape (narrow)'], 3 => [py0, '3 Panel + ribbons'],
               4 => [py1, '4 Foam tape'], 5 => [y_back, '5 Back plate'], 6 => [y_rear, '6 Board + arm block'],
               7 => [rear_geom(y_rear)[:lid_y], '7 Lid'] },
    }
  end

  def self.arm(e, m, h)
    bx, block_back, bz = h[:block]
    a1 = [bx, block_back + 18, bz]
    el = [bx, a1[1] + 75, bz + 85]
    cl = [bx, el[1] + 65, el[2] + 90]
    parts = [
      rod(e, 'Arm: 1/4" screw + ball head', h[:block], a1, 9, m[:metal]),
      rod(e, 'Arm: lower section', a1, el, 7, m[:metal]),
      rod(e, 'Arm: centre lock knob', [bx - 18, el[1], el[2]], [bx + 18, el[1], el[2]], 13, m[:knob]),
      rod(e, 'Arm: upper section', el, cl, 7, m[:metal]),
      rod(e, 'Wire bundle (along arm)', h[:wire_top], [bx + 10, el[1] + 4, el[2] - 6], 4, m[:wires]),
    ]
    shelf_z = cl[2] + 6
    parts << box(e, 'Super clamp', bx - 14, cl[1] - 8, cl[2] - 14, bx + 14, cl[1] + 30, shelf_z + 30, m[:metal])
    shelf = box(e, 'Desk shelf', -260, cl[1] + 4, shelf_z, 260, cl[1] + 264, shelf_z + 18, m[:shelf])
    [parts, shelf]
  end

  def self.tag(model, name)
    model.layers[name] || model.layers.add(name)
  end

  def self.translate(g, dx, dy = 0)
    g.transform!(Geom::Transformation.translation([dx.mm, dy.mm, 0]))
  end

  def self.build(model = Sketchup.active_model)
    p = P
    model.start_operation('E-ink mount mock-up', true)
    model.entities.grep(Sketchup::Group).select { |g| g.name.start_with?('E-ink mount') }.each(&:erase!)
    m = materials(model)
    lines = []
    r1 = ->(v) { v.round(1) }

    VARIANTS.each_with_index do |(key, title), i|
      x0 = i * p[:variant_dx]
      vg = model.entities.add_group
      vg.name = "E-ink mount: #{title}"
      vg.layer = tag(model, title)

      @parts = nil
      main = vg.entities.add_group
      main.name = "#{title} (assembled)"
      main.layer = tag(model, 'Assembled')
      h = send(key, main.entities, m)
      if key == :layered
        arm_parts, shelf = arm(main.entities, m, h)
        arm_parts.each { |g| g.layer = tag(model, 'Arm') }
        shelf.layer = tag(model, 'Shelf')
      end
      main.entities.add_text(title, pt(p[:explode_dx] / 2.0, 0, h[:z1] + 120))
      translate(main, x0)

      @parts = []
      ex = vg.entities.add_group
      ex.name = "#{title} (exploded)"
      ex.layer = tag(model, 'Exploded view')
      send(key, ex.entities, m)
      @parts.each { |g, s| translate(g, 0, (s - 1) * p[:explode_gap]) }
      h[:steps].each do |s, (y, text)|
        ex.entities.add_text(text, pt(h[:x0], y + (s - 1) * p[:explode_gap], h[:z1]),
                             Geom::Vector3d.new(0, 0, (18 + (s.even? ? 16 : 0)).mm))
      end
      @parts = nil
      translate(ex, x0 + p[:explode_dx])

      win = basics[:win]
      lines << "#{title}: #{r1[h[:x1] - h[:x0]]} x #{r1[h[:z1] - h[:z0]]} mm; " \
               "#{r1[h[:wood_depth]]} mm before the board, #{r1[h[:depth]]} mm to the lid; " \
               "visible border sides #{r1[h[:x1] - win[2]]}, top #{r1[h[:z1] - win[3]]}, bottom #{r1[win[1] - h[:z0]]} mm"
      lines << "  panel front to bezel gap #{r1[h[:gap]]} mm" if key == :layered
      lines << "  rebate depth to spare #{r1[h[:rebate_spare]]} mm" if h[:rebate_spare]
      lines << "  front tape at the sides must be cut to #{r1[h[:side_tape_w]]} mm wide" if h[:side_tape_w]
    end

    model.commit_operation
    @summary = lines.join("\n")
    puts @summary
  end
end

EinkMount.build unless defined?(EINK_NO_AUTOBUILD)
