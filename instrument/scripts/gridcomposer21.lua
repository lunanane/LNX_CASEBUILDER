-- ====================================================
-- MONOME III STANDALONE - ULTIMATE MULTI-TOOL ENGINE
-- ====================================================
print(">> Initializing Ultimate Multi-Tool Framework...")

-- ----------------------------------------------------
-- 1. GLOBAL FRAMEWORK CONFIG & CORE TRAFFIC ROUTERS
-- ----------------------------------------------------
current_page = 1
menu_active = false
GRID_WIDTH = 16 

MENU_X = GRID_WIDTH
MENU_Y = 8

pages = {}

-- MASTER ROUTER: UI DRAW CANVAS
function draw()
  grid_led_all(0)
  
  if pages[current_page] and pages[current_page].draw then
    pages[current_page]:draw()
  end
  
  if menu_active then
    for p_idx = 1, GRID_WIDTH do
      if p_idx == current_page then 
        grid_led(p_idx, 8, 15)
      elseif pages[p_idx] then 
        grid_led(p_idx, 8, 4)
      else 
        grid_led(p_idx, 8, 0) 
      end
    end
  end
  
  grid_led(MENU_X, MENU_Y, menu_active and 15 or 6)
  grid_refresh()
end

-- MASTER ROUTER: PHYSICAL GRID INPUTS
event_grid = function(x, y, z)
  if x == MENU_X and y == MENU_Y then
    if current_page == 3 and pages[3] and pages[3].col16_held_count > 0 then
      pages[3]:event(x, y, z)
      return
    end
    if z == 1 then menu_active = not menu_active end
    return
  end

  if menu_active then
    if y == 8 and z == 1 then
      if pages[x] then
        current_page = x
        menu_active = false
      end
    end
    return
  end

  if pages[current_page] and pages[current_page].event then
    pages[current_page]:event(x, y, z)
  end
end

-- ====================================================
-- HARDWARE INTERFACES & RAW MIDI CLOCK DECODER (SAMPLE ACCURATE)
-- ====================================================
clock_counter = 0
stutter_counter = 0
global_measure_pulses = 0 

function event_midi(byte1, byte2, byte3)
  if byte1 == 248 then
    clock_counter = clock_counter + 1
    global_measure_pulses = global_measure_pulses + 1
    
    if pages[3] and pages[3].clock_pulse_tick then
      pages[3]:clock_pulse_tick()
    end
    
    if global_measure_pulses >= 96 then 
      global_measure_pulses = 0 
      if pages[3] and pages[3].bar_tick then
        pages[3]:bar_tick()
      end
    end
    
    local seq = pages[2]
    if seq then
      if seq.armed_sync_reset and global_measure_pulses == 0 then
        seq.row8_held = {}
        seq.stutter_anchor = nil
        seq.armed_sync_reset = false
        seq.master_position = 1
        for row = 1, 7 do seq.positions[row] = 1 end
        seq:fire_current_notes()
        clock_counter = 0
        stutter_counter = 0
        print(">> PAGE 2 Downbeat Reset successfully captured!")
        return
      end
      
      if seq.stutter_anchor ~= nil then
        stutter_counter = stutter_counter + 1
        if stutter_counter >= seq.stutter_speed then
          stutter_counter = 0
          seq.flash_state = not seq.flash_state
          local lock_x = seq.stutter_anchor
          seq.master_position = lock_x
          for row = 1, 7 do
            seq.positions[row] = ((lock_x - 1) % seq.lengths[row]) + 1
          end
          seq:fire_current_notes()
        end
        if clock_counter >= 6 then clock_counter = 0 end
      else
        if clock_counter >= 6 then
          clock_counter = 0
          stutter_counter = 0
          seq:clock_tick()
        end
      end
    end
  end

  if byte1 == 250 or byte1 == 251 then
    clock_counter = 1 
    stutter_counter = 0
    global_measure_pulses = 0 
    
    if pages[2] then
      pages[2].master_position = 1
      for y = 1, 7 do pages[2].positions[y] = 1 end
      pages[2]:fire_current_notes()
    end
    
    if pages[3] then
      pages[3].bar_counter = 0
      pages[3].groove_step_pointer = 1
      pages[3].groove_pulse_accumulator = 0
      if pages[3].arranger_running then
        pages[3].current_stage = pages[3].loop_start
        pages[3]:fire_stage_chord()
      end
    end
    print(">> Hardware clocks aligned to downbeat.")
  end

  if byte1 == 252 then 
    print(">> MIDI Transport: STOP (Flushing Voice Engines)")
    if pages[3] then
      pages[3]:kill_all_chord_notes()
      pages[3]:kill_all_live_notes()
    end
    for note = 0, 127 do
      midi_note_off(note, 0, 1)
      midi_note_off(note, 0, 2)
      midi_note_off(note, 0, 3)
    end
  end
end

function framework_tick()
  for i = 1, #pages do 
    if pages[i] and pages[i].update then pages[i]:update() end 
  end
  draw()
end

-- ====================================================
-- PAGE 1: CHROMATIC INTERVALS PAD SURFACE
-- ====================================================
print(">> Defining Page 1 (Intervals)...")
pages[1] = {
  name = "intervals",
  ch = 1, vel = 1, alt = 0,
  display = {[0]=9, 0, 4, 0, 4, 4, 0, 4, 0, 4, 0, 4},
  velocity = {127, 112, 96, 80, 64, 32, 16, 1},
  held = {},
  note = function(self, x, y) return x + y*5 + 36 end,
  init = function(self) self.held = {} end,
  update = function(self) end,
  draw = function(self)
    if self.alt == 0 then
      for y=1,8 do
        for x=2, GRID_WIDTH do
          local n = self:note(x, y)
          grid_led(x, 9-y, self.display[n%12])
        end
      end
      for k,_ in pairs(self.held) do
        local hx = k % GRID_WIDTH
        if hx == 0 then hx = GRID_WIDTH end
        grid_led(hx, k//GRID_WIDTH, 15)
      end
      grid_led(1, self.vel, 15)
    else
      grid_led(1, 8, 15)
      grid_led(self.ch, 6, 9)
    end
  end,
  event = function(self, x, y, z)
    if x==1 and y==8 then self.alt = z
    elseif self.alt > 0 then if z==1 then self.ch = x end
    else
      if x==1 then self.vel = y
      else
        local n = self:note(x, 9-y)
        if z > 0 then
          midi_note_on(n, self.velocity[self.vel], self.ch)
          self.held[x+y*GRID_WIDTH] = true
        else
          midi_note_off(n, 0, self.ch)
          self.held[x+y*GRID_WIDTH] = nil
        end
      end
    end
  end
}

-- ====================================================
-- PAGE 2: 7-TRACK PER-ROW SEQUENCER & PERFORMANCE ENGINE
-- ====================================================
print(">> Defining Page 2 (Sequencer)...")
pages[2] = {
  name = "sequencer",
  steps = {}, positions = {}, lengths = {},
  master_position = 1, master_length = 16,
  row8_held = {}, stutter_anchor = nil, stutter_speed = 6, flash_state = false, armed_sync_reset = false,
  notes = {60, 61, 62, 63, 64, 65, 66}, ch = 2, first_press = {}, resize_action_happened = false,
  
  init = function(self)
    for y = 1, 7 do
      self.steps[y] = {}
      for x = 1, 16 do self.steps[y][x] = false end
      self.positions[y] = 1
      self.lengths[y] = 16
      self.first_press[y] = nil
    end
    self.master_position = 1
    self.master_length = 16
    self.row8_held = {}
    self.stutter_anchor = nil
    self.stutter_speed = 6
    self.flash_state = false
    self.armed_sync_reset = false
  end,
  
  recalculate_master_bounds = function(self)
    local max_len = 1
    for y = 1, 7 do if self.lengths[y] > max_len then max_len = self.lengths[y] end end
    self.master_length = max_len
    if self.master_position > self.master_length then self.master_position = 1 end
  end,

  recalculate_stutter_state = function(self)
    local lowest_x = 99 local highest_x = 0 local count = 0
    for x = 1, 15 do
      if self.row8_held[x] then
        count = count + 1
        if x < lowest_x then lowest_x = x end
        if x > highest_x then highest_x = x end
      end
    end
    
    if count == 0 then
      self.stutter_anchor = nil
      self.armed_sync_reset = false
    else
      self.stutter_anchor = lowest_x
      local span = highest_x - lowest_x
      if span == 0 then     self.stutter_speed = 6   
      elseif span == 1 then self.stutter_speed = 12  
      elseif span == 2 then self.stutter_speed = 8   
      elseif span == 3 then self.stutter_speed = 6   
      elseif span == 4 then self.stutter_speed = 4   
      else                  self.stutter_speed = 3   
      end
    end
  end,

  fire_current_notes = function(self)
    for y = 1, 7 do if self.steps[y][self.positions[y]] then midi_note_on(self.notes[y], 100, self.ch) end end
  end,

  clock_tick = function(self)
    self.master_position = self.master_position + 1
    if self.master_position > self.master_length then self.master_position = 1 end
    for y = 1, 7 do
      self.positions[y] = self.positions[y] + 1
      if self.positions[y] > self.lengths[y] then self.positions[y] = 1 end
    end
    self:fire_current_notes()
  end,
  
  update = function(self) end,
  
  draw = function(self)
    for y = 1, 7 do
      local len = self.lengths[y] local pos = self.positions[y]
      for x = 1, 16 do
        local led = 0
        if x == 1 and y == 7 and self.stutter_anchor ~= nil then
          led = self.armed_sync_reset and 15 or 4
        else
          if x <= len then
            if self.steps[y][x] then led = 8 else led = 2 end
            if x == 1 or x == len then led = math.max(led, 4) end
            if x == pos then led = 13 end
          end
        end
        grid_led(x, y, led)
      end
    end
    for x = 1, 15 do
      local led = 0
      if x <= self.master_length then
        if self.row8_held[x] then led = self.flash_state and 15 or 6
        elseif x == self.master_position and self.stutter_anchor == nil then led = 11 
        else led = 4 end
      end
      grid_led(x, 8, led)
    end
    grid_led(16, 8, 4) 
  end,
  
  event = function(self, x, y, z)
    if x == 1 and y == 7 and self.stutter_anchor ~= nil then
      if z == 1 then self.armed_sync_reset = true end
      return
    end
    if y == 8 then
      if x == 16 then return end 
      if z == 1 and x <= self.master_length then
        self.row8_held[x] = true
        self:recalculate_stutter_state()
        if self.stutter_anchor == x then
          self.master_position = x
          for row = 1, 7 do self.positions[row] = ((x - 1) % self.lengths[row]) + 1 end
          self.flash_state = true
          self:fire_current_notes()
        end
      else
        self.row8_held[x] = nil
        self:recalculate_stutter_state()
      end
      return
    end
    if z == 1 then
      if self.first_press[y] == nil then self.first_press[y] = x self.resize_action_happened = false
      else
        local start_key = self.first_press[y] local end_key = x
        if start_key == 1 and end_key > 1 then
          self.lengths[y] = end_key self.resize_action_happened = true
          if self.positions[y] > self.lengths[y] then self.positions[y] = 1 end
          self:recalculate_master_bounds()
        end
      end
    else
      if self.first_press[y] == x then
        if not self.resize_action_happened then self.steps[y][x] = not self.steps[y][x] end
        self.first_press[y] = nil self.resize_action_happened = false
      end
    end
  end
}

-- ====================================================
-- PAGE 3: HARMONIC MASTER ARRANGER & AUDITION FIX
-- ====================================================
print(">> Defining Page 3 (Harmonic Arranger)...")
pages[3] = {
  name = "chord_pads",
  ch = 3,
  
  circle_maj = {60, 67, 62, 69, 64, 71}, 
  circle_min = {57, 64, 59, 66, 61, 68}, 
  
  chord_types = {
    {0, 4, 7}, {0, 3, 7}, {0, 4, 7, 11}, {0, 3, 7, 10}, {0, 2, 7}, {0, 5, 7}, {0, 4, 7, 9}, {0, 4, 7, 10}, 
    {0, 3, 6}, {0, 4, 8}, {0, 4, 7, 11, 14}, {0, 3, 7, 10, 14}, {0, 3, 6, 9}, {0, 3, 7, 9}, {0, 3, 6, 10},
  },
  
  diatonic_map = {
    maj = {[1]=true, [3]=true, [5]=true, [6]=true, [7]=true, [11]=true},
    min = {[2]=true, [4]=true, [5]=true, [6]=true, [12]=true, [14]=true, [15]=true}
  },

  groove_library = {
    {1, 0, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0}, 
    {0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1}, 
    {1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1}, 
    {1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 1, 1, 0}, 
    {1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1}, 
    {1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 0, 0}, 
    {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, 
    {1, 0, 1, 0, 0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0}, 
  },
  
  selected_modifier = 1, last_live_root = nil, active_notes = {}, live_active_notes = {}, 
  durations = {}, assigned_roots = {}, assigned_mods = {}, assigned_modes = {}, assigned_grooves = {}, 
  selected_playmode = 2, selected_groove = 1, current_stage = 1, bar_counter = 0, 
  held_arranger_row = nil, blink_toggle = false, frame_counter = 0, arranger_running = true,

  loop_start = 1, loop_end = 8, col16_held_count = 0, col16_first_y = nil, col16_second_y = nil,
  active_retrigger_row = nil, retrigger_speed_pulses = 6, retrigger_clock_accumulator = 0,
  speed_lookup = {96, 48, 24, 12, 8, 6, 4, 3}, speed_lookup_inv_y = nil,
  groove_step_pointer = 1, groove_pulse_accumulator = 0,
  mode_pulse_dividers = { [3]=24, [4]=12, [5]=6, [6]=3 },

  -- NEW: Independent Live Audition Gate Registers
  live_pulse_counter = 0,
  live_step_pointer = 1,

  init = function(self)
    self.selected_modifier = 1 self.last_live_root = nil self.active_notes = {} self.live_active_notes = {}
    self.current_stage = 1 self.bar_counter = 0 self.held_arranger_row = nil self.blink_toggle = false 
    self.frame_counter = 0 self.selected_playmode = 2 self.selected_groove = 1 self.arranger_running = true
    self.loop_start = 1 self.loop_end = 8 self.groove_step_pointer = 1 self.groove_pulse_accumulator = 0
    self.active_retrigger_row = nil self.speed_lookup_inv_y = nil
    self.live_pulse_counter = 0 self.live_step_pointer = 1
    
    for y = 1, 8 do
      self.durations[y] = 1 self.assigned_roots[y] = nil self.assigned_mods[y] = 1
      self.assigned_modes[y] = 2 self.assigned_grooves[y] = 1 
    end
  end,
  
  get_scale_type_from_root = function(self, root)
    if root == nil then return nil end
    for y = 1, 6 do
      if self.circle_maj[y] == root or (self.circle_maj[y] + 6) == root then return "maj" end
      if self.circle_min[y] == root or (self.circle_min[y] + 6) == root then return "min" end
    end
    return nil
  end,

  get_coords_from_root = function(self, root)
    if root == nil then return nil end
    for y = 1, 6 do
      if self.circle_maj[y] == root then return {x=1, y=y} end
      if self.circle_min[y] == root then return {x=2, y=y} end
      if (self.circle_maj[y] + 6) == root then return {x=3, y=y} end
      if (self.circle_min[y] + 6) == root then return {x=4, y=y} end
    end
    return nil
  end,

  kill_all_chord_notes = function(self)
    for note, _ in pairs(self.active_notes) do midi_note_off(note, 0, self.ch) end
    self.active_notes = {}
  end,
  
  kill_all_live_notes = function(self)
    for note, _ in pairs(self.live_active_notes) do midi_note_off(note, 0, self.ch) end
    self.live_active_notes = {}
  end,

  fire_raw_row_notes = function(self, row_index)
    local root = self.assigned_roots[row_index] local mod_idx = self.assigned_mods[row_index]
    if root ~= nil then
      local intervals = self.chord_types[mod_idx] or {0}
      for _, interval in ipairs(intervals) do
        local note_out = root + interval
        if not self.live_active_notes[note_out] then
          midi_note_on(note_out, 95, self.ch)
          self.active_notes[note_out] = true
        end
      end
    end
  end,

  fire_stage_chord = function(self)
    self:kill_all_chord_notes()
    if not self.arranger_running or self.active_retrigger_row ~= nil then return end
    if self.assigned_modes[self.current_stage] == 2 then self:fire_raw_row_notes(self.current_stage) end
  end,

  -- FIXED: Unified real-time clock executor maps both automation AND manual live gates!
  clock_pulse_tick = function(self)
    -- 1. Performance Ribbon Overrides (Column 15)
    if self.active_retrigger_row ~= nil then
      self.retrigger_clock_accumulator = self.retrigger_clock_accumulator + 1
      if self.retrigger_clock_accumulator >= self.retrigger_speed_pulses then
        self.retrigger_clock_accumulator = 0
        self:kill_all_chord_notes()
        self:fire_raw_row_notes(self.active_retrigger_row)
      end
      return
    end

    -- 2. LIVE GATING PREVIEW ENGINE FOR MANUAL JAMMING
    -- If your finger is holding a root pad and a pulsed groove playmode is active, chop it!
    if self.last_live_root and self.selected_playmode >= 3 and self.selected_playmode <= 6 then
      local pulse_target = self.mode_pulse_dividers[self.selected_playmode] or 6
      self.live_pulse_counter = self.live_pulse_counter + 1
      
      if self.live_pulse_counter >= pulse_target then
        self.live_pulse_counter = 0
        local pattern = self.groove_library[self.selected_groove] or self.groove_library[1]
        
        self:kill_all_live_notes()
        if pattern[self.live_step_pointer] == 1 then
          local root_val = self:get_root_from_coords(self.last_live_root.x, self.last_live_root.y)
          if root_val then
            for _, interval in ipairs(self.chord_types[self.selected_modifier]) do
              local note_out = root_val + interval
              midi_note_on(note_out, 105, self.ch)
              self.live_active_notes[note_out] = true
            end
          end
        end
        self.live_step_pointer = self.live_step_pointer + 1
        if self.live_step_pointer > 16 then self.live_step_pointer = 1 end
      end
    end

    -- 3. AUTOMATED TIMELINE TIMING SEQUENCER LAYER
    if self.arranger_running and self.held_arranger_row == nil then
      local mode = self.assigned_modes[self.current_stage]
      if mode >= 3 and mode <= 6 then
        local pulse_target = self.mode_pulse_dividers[mode] or 6
        self.groove_pulse_accumulator = self.groove_pulse_accumulator + 1
        if self.groove_pulse_accumulator >= pulse_target then
          self.groove_pulse_accumulator = 0
          local pattern = self.groove_library[self.assigned_grooves[self.current_stage]] or self.groove_library[1]
          self:kill_all_chord_notes()
          if pattern[self.groove_step_pointer] == 1 then self:fire_raw_row_notes(self.current_stage) end
          self.groove_step_pointer = self.groove_step_pointer + 1
          if self.groove_step_pointer > 16 then self.groove_step_pointer = 1 end
        end
      end
    end
  end,

  bar_tick = function(self)
    if not self.arranger_running or self.active_retrigger_row ~= nil then return end 
    self.bar_counter = self.bar_counter + 1
    if self.bar_counter >= self.durations[self.current_stage] then
      self.bar_counter = 0
      self.current_stage = self.current_stage + 1
      self.groove_step_pointer = 1 self.groove_pulse_accumulator = 0
      if self.current_stage > self.loop_end or self.current_stage < self.loop_start then self.current_stage = self.loop_start end
      self:fire_stage_chord()
    end
  end,

  update = function(self)
    self.frame_counter = self.frame_counter + 1
    if self.frame_counter >= 16 then self.frame_counter = 0 end
    self.blink_toggle = (self.frame_counter < 8)
  end,

  draw = function(self)
    local target_view_stage = self.held_arranger_row or self.current_stage
    local step_root = self.assigned_roots[target_view_stage]
    local step_mod = self.assigned_mods[target_view_stage]
    local step_mode = self.assigned_modes[target_view_stage]
    local step_groove = self.assigned_grooves[target_view_stage]
    local flash_coords = self:get_coords_from_root(step_root)

    for y = 1, 6 do
      for x = 1, 4 do
        local led = (x == 1 or x == 3) and 6 or 3
        if self.held_arranger_row then
          if flash_coords and flash_coords.x == x and flash_coords.y == y and step_mode >= 2 then led = 15 end
        else
          if self.arranger_running and flash_coords and flash_coords.x == x and flash_coords.y == y and step_mode >= 2 then
            led = self.blink_toggle and 15 or 6 
          end
        end
        if self.last_live_root and y == self.last_live_root.y and x == self.last_live_root.x then led = 15 end
        grid_led(x, y, led)
      end
    end
    
    local are_grooves_awake = (step_mode >= 3 and step_mode <= 6) or (self.selected_playmode >= 3 and self.selected_playmode <= 6)
    local view_groove = self.held_arranger_row and step_groove or self.selected_groove
    for y = 5, 6 do
      for x = 5, 8 do
        local cell_groove_idx = ((y - 5) * 4) + (x - 4) local led = 1
        if are_grooves_awake then led = (cell_groove_idx == view_groove) and 15 or 5 end
        grid_led(x, y, led)
      end
    end
    
    for y = 1, 2 do
      for x = 5, 8 do
        local led = 1
        if y == 1 then
          if x == 5 then
            if self.held_arranger_row then led = (step_mode == 2) and 15 or 3
            else led = (self.arranger_running and step_mode == 2 and self.blink_toggle) and 15 or ((self.selected_playmode == 2) and 15 or 3) end
          elseif x == 8 then
            if self.held_arranger_row then led = (step_mode == 1) and 15 or 3
            else led = (self.arranger_running and step_mode == 1 and self.blink_toggle) and 15 or ((self.selected_playmode == 1) and 15 or 3) end
          end
        elseif y == 2 then
          local mode_mapped_idx = x - 2
          if self.held_arranger_row then led = (step_mode == mode_mapped_idx) and 15 or 3
          else led = (self.arranger_running and step_mode == mode_mapped_idx) and (self.blink_toggle and 15 or 4) or ((self.selected_playmode == mode_mapped_idx) and 10 or 3) end
        end
        grid_led(x, y, led)
      end
    end
    
    local target_root_reference = nil
    if self.held_arranger_row then target_root_reference = self.assigned_roots[self.held_arranger_row]
    elseif self.last_live_root and self.last_live_root.y <= 6 then target_root_reference = self:get_root_from_coords(self.last_live_root.x, self.last_live_root.y)
    elseif self.arranger_running then target_root_reference = step_root end
    local active_scale_flavor = self:get_scale_type_from_root(target_root_reference)

    for y = 7, 8 do
      for x = 1, 8 do
        if not (x == 8 and y == 8) then
          local mod_idx = ((y - 7) * 8) + x
          if mod_idx <= 15 then 
            local led = 2 
            if active_scale_flavor and self.diatonic_map[active_scale_flavor][mod_idx] then led = 6 end
            if self.held_arranger_row then
              if step_mod == mod_idx and step_mode >= 2 then led = 15 end
            else
              if self.arranger_running and step_mod == mod_idx and step_mode >= 2 then led = self.blink_toggle and 15 or led end
              if mod_idx == self.selected_modifier then led = 15 end 
            end
            grid_led(x, y, led)
          end
        end
      end
    end
    
    if self.arranger_running then grid_led(8, 8, 4) else grid_led(8, 8, self.blink_toggle and 15 or 2) end
    for y = 1, 8 do grid_led(9, y, 1) end

    for y = 1, 8 do
      local target_col = 8 + self.durations[y] 
      for x = 9, 14 do
        local led = 2
        if x == target_col then led = (y == self.current_stage) and (self.blink_toggle and 15 or 4) or 15
        else if y == self.current_stage and self.arranger_running then led = 3 end end
        grid_led(x, y, led)
      end
      
      local col15_led = 3
      if self.active_retrigger_row ~= nil and y == self.speed_lookup_inv_y then col15_led = self.blink_toggle and 15 or 4 end
      grid_led(15, y, col15_led)
      
      local col16_led = 2
      if y >= self.loop_start and y <= self.loop_end then col16_led = 6 end
      if y == self.current_stage then col16_led = self.blink_toggle and 15 or 4 end
      grid_led(16, y, col16_led)
    end
  end,

  event = function(self, x, y, z)
    if x == 16 then
      if z == 1 then
        self.col16_held_count = self.col16_held_count + 1
        if self.col16_held_count == 1 then
          self.col16_first_y = y self.current_stage = y self.bar_counter = 0
          self:fire_stage_chord()
        elseif self.col16_held_count == 2 then
          self.col16_second_y = y
        end
      else
        if z == 0 and self.col16_held_count == 2 then
          if self.col16_first_y and self.col16_second_y then
            self.loop_start = math.min(self.col16_first_y, self.col16_second_y)
            self.loop_end = math.max(self.col16_first_y, self.col16_second_y)
          end
        elseif z == 0 and self.col16_held_count == 1 then
          if self.col16_second_y == nil then self.loop_start = y self.loop_end = y end
        end
        self.col16_held_count = self.col16_held_count - 1
        if self.col16_held_count == 0 then self.col16_first_y = nil self.col16_second_y = nil end
      end
      return
    end

    if x == 15 then
      if z == 1 then
        local target_chord_row = (self.col16_held_count > 0) and self.col16_first_y or self.current_stage
        self.active_retrigger_row = target_chord_row
        self.retrigger_speed_pulses = self.speed_lookup[y] or 6
        self.speed_lookup_inv_y = y self.retrigger_clock_accumulator = 0
        self:kill_all_chord_notes()
        self:fire_raw_row_notes(target_chord_row)
      else
        if self.speed_lookup_inv_y == y then
          self.active_retrigger_row = nil self.speed_lookup_inv_y = nil
          self:fire_stage_chord() 
        end
      end
      return
    end

    if x >= 5 and x <= 8 and y >= 5 and y <= 6 then
      if z == 1 then
        local target_view_stage = self.held_arranger_row or self.current_stage
        local chosen_groove_idx = ((y - 5) * 4) + (x - 4)
        self.selected_groove = chosen_groove_idx
        
        if self.held_arranger_row then
          self.assigned_grooves[self.held_arranger_row] = chosen_groove_idx
        else
          self.assigned_grooves[self.current_stage] = chosen_groove_idx
        end
        self.groove_step_pointer = 1 self.groove_pulse_accumulator = 0
        self.live_step_pointer = 1 self.live_pulse_counter = 0
      end
      return
    end

    if x >= 9 and x <= 14 then
      if z == 1 then
        self.held_arranger_row = y self.durations[y] = x - 8   
        if self.assigned_roots[y] ~= nil then self.selected_modifier = self.assigned_mods[y] end
        self.selected_playmode = self.assigned_modes[y]
        self.selected_groove = self.assigned_grooves[y]
      else
        if self.held_arranger_row == y then self.held_arranger_row = nil self:fire_stage_chord() end
      end
      return
    end

    if x == 8 and y == 8 then
      if z == 1 then
        self.arranger_running = not self.arranger_running
        if self.arranger_running then self:fire_stage_chord() else self:kill_all_chord_notes() end
      end
      return
    end

    if x >= 5 and x <= 8 and y <= 2 then
      if z == 1 then
        local target_view_stage = self.held_arranger_row or self.current_stage
        if y == 1 then
          if x == 5 then self.selected_playmode = 2 self.assigned_modes[target_view_stage] = 2
          elseif x == 8 then self.selected_playmode = 1 self.assigned_modes[target_view_stage] = 1 self:kill_all_chord_notes() end
        elseif y == 2 then
          local computed_mode = x - 2 self.selected_playmode = computed_mode self.assigned_modes[target_view_stage] = computed_mode
          self.groove_step_pointer = 1 self.groove_pulse_accumulator = 0
          self.live_step_pointer = 1 self.live_pulse_counter = 0
        end
      end
      return
    end

    if y >= 7 and y <= 8 and x <= 8 then
      if z == 1 then
        local mod_idx = ((y - 7) * 8) + x
        if mod_idx <= 15 then 
          self.selected_modifier = mod_idx
          if self.held_arranger_row then self.assigned_mods[self.held_arranger_row] = mod_idx end
          
          -- FIXED LIVE UPDATE PREVIEW: 
          -- If jamming on chords live in standard mode (2), fire note immediately!
          if self.last_live_root and self.last_live_root.y <= 6 and self.selected_playmode == 2 then
            self:kill_all_live_notes() 
            local root_val = self:get_root_from_coords(self.last_live_root.x, self.last_live_root.y)
            if root_val then
              for _, interval in ipairs(self.chord_types[mod_idx]) do
                local note_out = root_val + interval
                midi_note_on(note_out, 100, self.ch)
                self.live_active_notes[note_out] = true
              end
            end
          end
        end
      end
      return
    end

    if x <= 4 and y <= 6 then
      if z == 1 then
        self.last_live_root = {x = x, y = y}
        local root_val = self:get_root_from_coords(x, y)
        if root_val then
          if self.held_arranger_row then
            local target_slot = self.held_arranger_row
            self.assigned_roots[target_slot] = root_val
            self.assigned_mods[target_slot] = self.selected_modifier
            self.assigned_modes[target_slot] = self.selected_playmode 
          end
          
          -- FIXED LIVE AUDITION LOGIC: 
          -- If in standard chord mode, sustain note immediately. 
          -- If in groove mode, the clock engine will automatically catch this variable and pulse it!
          if self.selected_playmode == 2 then
            for _, interval in ipairs(self.chord_types[self.selected_modifier]) do
              local note_out = root_val + interval
              if not self.live_active_notes[note_out] then
                midi_note_on(note_out, 105, self.ch) 
                self.live_active_notes[note_out] = true
              end
            end
          elseif self.selected_playmode >= 3 then
            -- Prime clock registers so the pulse engine hits step 1 cleanly on downbeat press
            self.live_pulse_counter = 999 
            self.live_step_pointer = 1
          end
        end
      else
        if self.last_live_root and self.last_live_root.x == x and self.last_live_root.y == y then
          self.last_live_root = nil
          self:kill_all_live_notes()
          if self.held_arranger_row == nil then self:fire_stage_chord() end
        end
      end
    end
  end,

  get_root_from_coords = function(self, x, y)
    if x == 1 then return self.circle_maj[y] end if x == 2 then return self.circle_min[y] end
    if x == 3 then return self.circle_maj[y] + 6 end if x == 4 then return self.circle_min[y] + 6 end
    return nil
  end
}

-- ====================================================
-- ENGINE LIFECYCLE DISPATCH
-- ====================================================
print(">> Powering up internal lifecycle contexts...")
for i = 1, #pages do pages[i]:init() end

m_main = metro.init(framework_tick, 0.03)
m_main:start()

grid_refresh()
print(">> Live mode rhythmic previews fully repaired!")