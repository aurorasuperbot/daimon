-- DAIMON locked WezTerm config — DO NOT EDIT
--
-- This file is rewritten on every `daimon install` (and on every
-- `daimon launch`) so that the render surface every player sees is
-- pixel-identical regardless of any user-side ~/.wezterm.lua.
--
-- If you want to customise the look of DAIMON, file an issue — we ship
-- one configuration on purpose so card art renders at a known DPI / cell
-- size / colour space.

local wezterm = require 'wezterm'
local config  = wezterm.config_builder and wezterm.config_builder() or {}

-- ---------------------------------------------------------------------------
-- Identity / chrome
-- ---------------------------------------------------------------------------

config.window_decorations          = "TITLE | RESIZE"
config.window_close_confirmation   = "NeverPrompt"
config.enable_tab_bar              = false
config.use_fancy_tab_bar           = false
config.audible_bell                = "Disabled"
config.check_for_updates           = false   -- daimon manages WezTerm updates
config.exit_behavior               = "Close"

wezterm.on('format-window-title', function(tab, pane, tabs, panes, conf)
  return 'DAIMON'
end)

-- ---------------------------------------------------------------------------
-- Window geometry
-- The widest TUI (loadout editor) is 145 cells × ~36 rows. We over-allocate
-- so the player has slack for status lines + headers.
-- ---------------------------------------------------------------------------

config.initial_cols = 150
config.initial_rows = 42

config.window_padding = {
  left = 8, right = 8, top = 6, bottom = 6,
}

-- ---------------------------------------------------------------------------
-- Font: Cascadia Mono is a sane cross-platform default and bundled with
-- WezTerm itself (Cascadia Code is in the wezterm tarball). We set a
-- locked size so card-art tile measurements stay constant.
-- ---------------------------------------------------------------------------

config.font = wezterm.font_with_fallback {
  "Cascadia Mono",
  "Menlo",
  "Consolas",
  "DejaVu Sans Mono",
  "Liberation Mono",
}
config.font_size           = 12.5
config.line_height         = 1.0
config.cell_width          = 1.0
config.harfbuzz_features   = { "calt=0", "clig=0", "liga=0" }  -- no ligatures (alignment matters)

-- ---------------------------------------------------------------------------
-- Colour scheme — dark background tuned to make 832×1216 NovelAI card
-- art read with high saturation against the chrome.
-- ---------------------------------------------------------------------------

config.color_scheme = "Builtin Dark"
config.colors = {
  background        = "#0c0c10",
  foreground        = "#e0e0e8",
  cursor_bg         = "#75c0ff",
  cursor_fg         = "#0c0c10",
  cursor_border     = "#75c0ff",
  selection_bg      = "#1f3358",
  selection_fg      = "#e0e0e8",

  ansi = {
    "#282a36",  -- 0 black
    "#eb5a50",  -- 1 red
    "#5ad282",  -- 2 green
    "#e6c65a",  -- 3 yellow
    "#50a0eb",  -- 4 blue
    "#c882dc",  -- 5 magenta
    "#82dcdc",  -- 6 cyan
    "#dcdcdc",  -- 7 white
  },
  brights = {
    "#6e7684",  -- 8 bright black
    "#ff8278",  -- 9 bright red
    "#82f0a0",  -- 10 bright green
    "#ffdc78",  -- 11 bright yellow
    "#78beff",  -- 12 bright blue
    "#dca0f0",  -- 13 bright magenta
    "#a0f0f0",  -- 14 bright cyan
    "#ffffff",  -- 15 bright white
  },
}

-- ---------------------------------------------------------------------------
-- Image rendering: WezTerm supports the Kitty Graphics Protocol natively
-- and is used by `daimon/render/kgp.py` for pixel-perfect card art.
-- Nothing to enable explicitly; just don't disable it.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Scrollback: this is a single-screen game UI; no need for unbounded
-- scrollback eating RAM.
-- ---------------------------------------------------------------------------

config.scrollback_lines = 2000

-- ---------------------------------------------------------------------------
-- Key bindings: all default WezTerm bindings are inert here because the
-- DAIMON TUIs intercept input themselves. We just disable the few that
-- could surprise players (split, copy-mode, etc.).
-- ---------------------------------------------------------------------------

config.disable_default_key_bindings = true
config.keys = {
  -- Allow Ctrl-C to interrupt the running daimon command.
  { key = "c", mods = "CTRL", action = wezterm.action.SendKey { key = "c", mods = "CTRL" } },
  -- Allow paste — useful when the agent surfaces a card_id to type back.
  { key = "v", mods = "CTRL|SHIFT", action = wezterm.action.PasteFrom 'Clipboard' },
  -- Allow copy — useful when the player wants to share a state_id.
  { key = "c", mods = "CTRL|SHIFT", action = wezterm.action.CopyTo  'Clipboard' },
  -- Allow font size adjust on a pinch (a11y).
  { key = "=", mods = "CTRL", action = wezterm.action.IncreaseFontSize },
  { key = "-", mods = "CTRL", action = wezterm.action.DecreaseFontSize },
  { key = "0", mods = "CTRL", action = wezterm.action.ResetFontSize },
}

return config
