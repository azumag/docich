/* Hooks for a curses game display for GNU Robots */

/* Copyright (C) 1998 Jim Hall, jhall1@isd.net */

/*
   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 2 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program; if not, write to the Free Software
   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

#include <curses.h>
#include <stdlib.h>		/* for sleep */
#include "hooks.h"


/* Functions in this file */

void curses_print_string (const char *s);

/* Live status line under the map (score/energy/shields).  The globals
   live in main.c; nrows is the drawn map height in rows. */
extern long robot_score;
extern long robot_energy;
extern long robot_shields;
extern int nrows;

static void
update_status_line (void)
{
  mvprintw (nrows + 1, 1,
            "Score: %ld   Energy: %ld   Shields: %ld       ",
            (robot_score < 0 ? 0 : robot_score),
            (robot_energy < 0 ? 0 : robot_energy),
            (robot_shields < 0 ? 0 : robot_shields));
  refresh ();
}


/* hooks to add things to the displayed map */

/* note that hook_delete_thing(x,y) is the same as
   hook_add_thing(x,y,space) */

void
hook_add_thing (int x, int y, int thing)
{
  /* Highlight the unusual chars */

  if (thing == '?')
    {
      standout ();
    }

  mvaddch (y, x, thing);

  if (thing == '?')
    {
      standend ();
    }

  refresh ();
  update_status_line ();
}

void
hook_add_robot (int x, int y, int cdir)
{
  standout ();
  switch (cdir)
    {
    case 'N':
      mvaddch (y, x, '^');
      break;
    case 'E':
      mvaddch (y, x, '>');
      break;
    case 'W':
      mvaddch (y, x, '<');
      break;
    case 'S':
      mvaddch (y, x, 'v');
      break;
    }

  standend ();
  refresh ();
  update_status_line ();
}

/* hooks to animate the robot */

void
hook_robot_smell (int x, int y, int cdir)
{
  curses_print_string ("robot smells..");
  update_status_line ();
}

void
hook_robot_zap (int x, int y, int cdir, int x_to, int y_to)
{
  curses_print_string ("robot fires little gun..");
}

void
hook_robot_feel (int x, int y, int cdir, int x_to, int y_to)
{
  curses_print_string ("robot feels for thing..");
}

void
hook_robot_look (int x, int y, int cdir, int x_to, int y_to)
{
  curses_print_string ("robot looks for thing..");
}

void
hook_robot_grab (int x, int y, int cdir, int x_to, int y_to)
{
  curses_print_string ("robot grabs thing..");
}

void
hook_init (void)
{
  /* Initialize curses mode */

  initscr ();
  cbreak ();
  noecho ();

  nonl ();
  intrflush (stdscr, FALSE);
  keypad (stdscr, TRUE);

  clear ();
  refresh ();
}

void
hook_close (void)
{
  /* End curses mode */

  endwin ();
}

void
curses_print_string (const char *s)
{
  /* print on mode line (y=LINES-1) */

  mvaddstr (LINES - 1, 1, s);
  refresh ();

  /* Sleep, then erase it */

  napms (500);
  /*  sleep (1); */

  move (LINES - 1, 1);
  clrtoeol ();
  refresh ();
}
