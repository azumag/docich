# GNU Robots (migrated build for docich)

## What this is

GNU Robots 1.0D (Jim Hall, GPL-2.0) fetched from
https://archive.org/details/gnurobots (item "gnurobots source code",
tarball `gnurobots-1.0D.tar.gz`) and migrated to build against **GNU
Guile 3.x** on modern systems (the original targeted Guile 1.2/1.3 and
its `gh_*` ABI no longer exists).

Why 1.0D and not 1.2.0: 1.0D has a **curses UI** that runs in a plain
80x24 tmux pane, which is exactly what the docich `[adapter = "cli"]`
pipeline (observe/act/presentation/stream) expects.  1.2.0 requires
GTK2 + VTE + X11 and would need a visual pipeline instead.

## Changes from the upstream 1.0D tarball

- `src/main.c`: `gh_enter` -> `scm_boot_guile` (+ thunk adapter),
  `gh_new_procedure{0,1}_0` -> `scm_c_define_gsubr`,
  `gh_eval_file` -> `scm_c_primitive_load`, `<guile/gh.h>` -> `<libguile.h>`.
- `src/api.c`: `gh_scm2int` -> `scm_to_int`, `gh_scm2newstr(s, NULL)` ->
  `scm_to_locale_string(s)`, `gh_long2scm` -> `scm_from_long`.
- `lib/curses.c`: added a live status line (`Score/Energy/Shields`)
  under the map (the original curses UI never displayed them; the X11
  UI did).  Printed to stdout at exit as before (STATISTICS block).
- `src/random.c`: gutted (Guile 3 provides `random` natively; the old
  file used the removed `gh_*` ABI and would clash with Guile's own
  `scm_random` symbol).
- `configure`: X11 and libXpm checks made non-fatal (only the unused
  `xrobots` target needs them), the Guile check now tests
  `scm_boot_guile` in `-lguile-3.0`.

## Build (VM, Ubuntu 24.04, aarch64)

```
sudo apt-get install -y guile-3.0-dev libncurses-dev pkg-config
./configure
cd src && make robots \
  "CC=gcc" \
  "CFLAGS=-I../include -DX_DISPLAY_MISSING=1 -DHAVE_NCURSES_H=1 -DHAVE_LIBM=1 -DSTDC_HEADERS=1 -DHAVE_UNISTD_H=1 -g -O2 -I/usr/include/guile/3.0" \
  "LDFLAGS=" "LDLIBS=-lm -lguile-3.0 -lgc -lpthread -ldl" "CURSES_LIBS=-lncurses"
```

Installed on the VM as `/usr/local/bin/gnurobots` with
`/usr/local/share/gnurobots/{maps,scheme}`.

## Run

```
gnurobots -f /usr/local/share/gnurobots/maps/maze.map \
          /usr/local/share/gnurobots/scheme/greedy.scm
```

- The robot program (Scheme) runs to completion: the match ends when the
  program ends or the robot dies (out of energy / destroyed).
- Final statistics (`Shields/Energy/Score`) are printed to stdout on
  exit; a live `Score: N Energy: N Shields: N` line is drawn under the
  map while running.
