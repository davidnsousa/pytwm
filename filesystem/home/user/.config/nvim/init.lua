-- disable netrw at the very start of your init.lua
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1

-- optionally enable 24-bit colour
vim.opt.termguicolors = true

vim.o.number = true

-- clipboard
vim.opt.clipboard = "unnamedplus"

-- Bootstrap lazy.nvim
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not (vim.uv or vim.loop).fs_stat(lazypath) then
  local lazyrepo = "https://github.com/folke/lazy.nvim.git"
  local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=stable", lazyrepo, lazypath })
  if vim.v.shell_error ~= 0 then
    vim.api.nvim_echo({
      { "Failed to clone lazy.nvim:\n", "ErrorMsg" },
      { out, "WarningMsg" },
      { "\nPress any key to exit..." },
    }, true, {})
    vim.fn.getchar()
    os.exit(1)
  end
end
vim.opt.rtp:prepend(lazypath)

-- Make sure to setup `mapleader` and `maplocalleader` before
-- loading lazy.nvim so that mappings are correct.
-- This is also a good place to setup other settings (vim.opt)
vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

-- Setup lazy.nvim
require("lazy").setup({
  spec = {
    { "catppuccin/nvim", name = "catppuccin", priority = 1000 },
    {
    "akinsho/bufferline.nvim",
    version = "*",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    },
    {
    'nvim-telescope/telescope.nvim', tag = '0.1.5',
    requires = { 'nvim-lua/plenary.nvim' }
    },
    {
    "nvim-tree/nvim-tree.lua",
    version = "*",
    lazy = false,
    dependencies = {
	"nvim-tree/nvim-web-devicons",
    },
    },
    {
    "folke/which-key.nvim",
    event = "VeryLazy",
    opts = {
    -- your configuration comes here
    -- or leave it empty to use the default settings
    -- refer to the configuration section below
    },
    },
    {
    'nvim-lualine/lualine.nvim',
    dependencies = { 'nvim-tree/nvim-web-devicons' }
    },
    {
    "NeogitOrg/neogit",
    lazy = true,
    dependencies = {
   	"nvim-lua/plenary.nvim",         -- required
    	"sindrets/diffview.nvim",        -- optional - Diff integration

    	-- Only one of these is needed.
    	"nvim-telescope/telescope.nvim", -- optional
    	"ibhagwan/fzf-lua",              -- optional
    	"nvim-mini/mini.pick",           -- optional
    	"folke/snacks.nvim",             -- optional
  	},
  	cmd = "Neogit",
  	keys = {
    		{ "<leader>gg", "<cmd>Neogit<cr>", desc = "Show Neogit UI" }
  	}
     },
    {
    -- renders Markdown in the buffer: headings, tables, lists, code blocks
    "MeanderingProgrammer/render-markdown.nvim",
    ft = { "markdown" },
    dependencies = { "nvim-tree/nvim-web-devicons" },
    opts = {},
    },
    {
    -- a scrollbar on the right of each window; it can be dragged with the mouse
    "dstein64/nvim-scrollview",
    opts = {},
    },
},
  -- Configure any other settings here. See the documentation for more details.
  -- colorscheme that will be used when installing plugins.
  install = { colorscheme = { "habamax" } },
  -- automatically check for plugin updates
  checker = { enabled = true },
})

require("nvim-tree").setup {}
require("bufferline").setup {}
require("telescope").setup {}
require("which-key").setup {}
require("lualine").setup {}

vim.keymap.set("n", "<leader>f", ":Telescope current_buffer_fuzzy_find<CR>", { desc = "Find", noremap = true  })
vim.keymap.set("n", "<leader>b", ":Telescope buffers<CR>", { desc = "Buffers", noremap = true  })
vim.keymap.set("n", "<leader>t", ":NvimTreeToggle<CR>", { desc = "File browser" , noremap = true })
vim.keymap.set("n", "<leader>m", ":RenderMarkdown toggle<CR>", { desc = "Markdown rendering on/off", noremap = true })

vim.cmd.colorscheme "catppuccin"
