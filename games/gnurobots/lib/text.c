/* Hooks for a pure text game display (log file) for GNU Robots */

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

#include <stdio.h>
#include "hooks.h"

/* hooks to add things to the displayed map */

/* note that hook_delete_thing(x,y) is the same as
   hook_add_thing(x,y,space) */

void
hook_add_thing (int x, int y, int thing)
{
  printf ("thing `%c' added at %d,%d\n", thing, x, y);
}

void
hook_add_robot (int x, int y, int cdir)
{
  printf ("robot now at %d,%d facing %c\n", x, y, cdir);
}

/* hooks to animate the robot */

void
hook_robot_smell (int x, int y, int cdir)
{
  printf ("the robot sniffs..\n");
}

void
hook_robot_zap (int x, int y, int cdir, int x_to, int y_to)
{
  printf ("robot fires gun at space %d,%d\n", x_to, y_to);
}

void
hook_robot_feel (int x, int y, int cdir, int x_to, int y_to)
{
  printf ("robot feels space %d,%d\n", x_to, y_to);
}

void
hook_robot_look (int x, int y, int cdir, int x_to, int y_to)
{
  printf ("robot looks %c from %d,%d\n", cdir, x, y);
}

void
hook_robot_grab (int x, int y, int cdir, int x_to, int y_to)
{
  printf ("robot grabs for space %d,%d\n", x_to, y_to);
}

void
hook_init (void)
{
  printf ("GNU Robots starting..\n");
}

void
hook_close (void)
{
  printf ("GNU Robots done.\n");
}
