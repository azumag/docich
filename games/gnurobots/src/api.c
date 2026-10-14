/* $Id: api.c,v 1.8 2000/05/29 03:01:50 jhall Exp $ */

/* Robot API for the GNU Robots game */

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
#include <stdlib.h>		/* for abs, free */

#include <libguile.h>		/* GNU Guile high */

#include "api.h"		/* GNU Robots API */
#include "hooks.h"		/* GNU Robots UI hooks */
#include "main.h"		/* for exit_nicely */
#include "sign.h"		/* a hack for +/- sign */


/* Globals (from main.c) */

extern int **map;
extern int nrows;
extern int ncols;

extern int robot_x;
extern int robot_y;
extern int robot_dir;

extern long robot_score;
extern long robot_energy;
extern long robot_shields;


/* Globals.  (this file only) */

int cdirs[] = { 'N', 'E', 'S', 'W' };

char *things[] = { "space", "food", "prize", "wall", "baddie", "robot" };
int cthings[] = { SPACE, FOOD, PRIZE, WALL, BADDIE, ROBOT };


/* Functions */

SCM robot_turn (SCM s_n)
{
  int n;
  int i;
  int incr;

  n = scm_to_int (s_n);

  /* turn left or right? */

  incr = sign (n);

  for (i = 0; i < abs (n); i++)
    {
      robot_dir += incr;

      if (robot_dir > 3)
	{
	  robot_dir = 0;
	}

      else if (robot_dir < 0)
	{
	  robot_dir = 3;
	}

      /* animate the robot */

      hook_add_robot (robot_x, robot_y, cdirs[robot_dir]);

      robot_energy -= 2;
      if (robot_energy < 1)
	{
	  exit_nicely ();
	}
    }				/* for */

  return (SCM_BOOL_T);
}

SCM robot_move (SCM s_n)
{
  int x_to, y_to;
  int dx, dy;
  int i;
  int n;

  n = scm_to_int (s_n);

  /* determine changes to x,y */

  switch (robot_dir)
    {
    case 0:			/* N */
      dx = 0;
      dy = -1 * sign (n);
      break;
    case 1:			/* E */
      dx = sign (n);
      dy = 0;
      break;
    case 2:			/* S */
      dx = 0;
      dy = sign (n);
      break;
    case 3:			/* W */
      dx = -1 * sign (n);
      dy = 0;
      break;
    }

  /* Move the robot */

  for (i = 0; i < abs (n); i++)
    {
      /* check for a space */

      x_to = robot_x + dx;
      y_to = robot_y + dy;

      /* no matter what, this took energy */

      robot_energy -= 2;
      if (robot_energy < 1)
	{
	  exit_nicely ();
	}

      switch (map[y_to][x_to])
	{
	case SPACE:		/* space */
	  /* move the robot there */

	  map[robot_y][robot_x] = SPACE;
	  map[y_to][x_to] = ROBOT;
	  hook_add_thing (robot_x, robot_y, SPACE);
	  hook_add_robot (x_to, y_to, cdirs[robot_dir]);
	  robot_x = x_to;
	  robot_y = y_to;

	  break;

	case BADDIE:		/* baddie */
	  /* Damage */

	  robot_shields -= 10;
	  if (robot_shields < 1)
	    {
	      exit_nicely ();
	    }

	  return (SCM_BOOL_F);

	  break;

	case WALL:		/* wall */
	  /* less damage */

	  robot_shields -= 2;
	  if (robot_shields < 1)
	    {
	      exit_nicely ();
	    }

	  return (SCM_BOOL_F);

	  break;

	default:
	  /* even less damage */

	  if (--robot_shields < 1)
	    {
	      exit_nicely ();
	    }

	  return (SCM_BOOL_F);

	  break;
	}
    }				/* for */

  return (SCM_BOOL_T);
}

SCM robot_smell (SCM s_th)
{
  int th;
  int i, j;
  char *str;

  str = scm_to_locale_string (s_th);
  th = what_thing (str);
  free (str);

  /* no matter what, this took energy */

  if (--robot_energy < 1)
    {
      exit_nicely ();
    }

  /* Smell for the thing */

  for (i = robot_x - 1; i <= robot_x + 1; i++)
    {
      for (j = robot_y - 1; j <= robot_y + 1; j++)
	{
	  if (!(i == robot_x && j == robot_y) && map[j][i] == th)
	    {
	      /* Found it */

	      return (SCM_BOOL_T);
	    }
	}			/* for */
    }				/* for */

  /* Failed to find it */

  return (SCM_BOOL_F);
}

SCM robot_feel (SCM s_th)
{
  int th;
  int x_to, y_to;
  int dx, dy;
  char *str;

  str = scm_to_locale_string (s_th);
  th = what_thing (str);
  free (str);

  /* determine changes to x,y */

  switch (robot_dir)
    {
    case 0:			/* N */
      dx = 0;
      dy = -1;
      break;
    case 1:			/* E */
      dx = 1;
      dy = 0;
      break;
    case 2:			/* S */
      dx = 0;
      dy = 1;
      break;
    case 3:			/* W */
      dx = -1;
      dy = 0;
      break;
    }

  /* no matter what, this took energy */

  if (--robot_energy < 1)
    {
      exit_nicely ();
    }

  /* Feel for the thing */

  x_to = robot_x + dx;
  y_to = robot_y + dy;

  hook_robot_feel (robot_x, robot_y, cdirs[robot_dir], x_to, y_to);

  if (map[y_to][x_to] == BADDIE)
    {
      /* touching a baddie is hurtful */
      if (robot_shields < 1)
	{
	  exit_nicely ();
	}
    }

  if (map[y_to][x_to] == th)
    {
      return (SCM_BOOL_T);
    }

  /* Did not feel it */

  return (SCM_BOOL_F);
}

SCM robot_look (SCM s_th)
{
  int th;
  int x_to, y_to;
  int dx, dy;
  char *str;

  str = scm_to_locale_string (s_th);
  th = what_thing (str);
  free (str);

  /* determine changes to x,y */

  switch (robot_dir)
    {
    case 0:			/* N */
      dx = 0;
      dy = -1;
      break;
    case 1:			/* E */
      dx = 1;
      dy = 0;
      break;
    case 2:			/* S */
      dx = 0;
      dy = 1;
      break;
    case 3:			/* W */
      dx = -1;
      dy = 0;
      break;
    }

  /* no matter what, this took energy */

  if (--robot_energy < 1)
    {
      exit_nicely ();
    }

  /* Look for the thing */

  x_to = robot_x + dx;
  y_to = robot_y + dy;

  while (map[y_to][x_to] == SPACE)
    {
      /* move the focus */

      x_to += dx;
      y_to += dy;
    }

  /* Outside the loop, we have found something */

  if (map[y_to][x_to] == th)
    {
      return (SCM_BOOL_T);
    }

  /* else, we did not find it */

  return (SCM_BOOL_F);
}

SCM robot_grab (void)
{
  int x_to, y_to;
  int dx, dy;

  /* determine changes to x,y */

  switch (robot_dir)
    {
    case 0:			/* N */
      dx = 0;
      dy = -1;
      break;
    case 1:			/* E */
      dx = 1;
      dy = 0;
      break;
    case 2:			/* S */
      dx = 0;
      dy = 1;
      break;
    case 3:			/* W */
      dx = -1;
      dy = 0;
      break;
    }

  /* Try to grab the thing */

  x_to = robot_x + dx;
  y_to = robot_y + dy;

  robot_energy -= 5;
  if (robot_energy < 1)
    {
      exit_nicely ();
    }
  hook_robot_grab (robot_x, robot_y, cdirs[robot_dir], x_to, y_to);

  /* Did we grab it? */

  switch (map[y_to][x_to])
    {
    case SPACE:
    case WALL:
    case ROBOT:
      return (SCM_BOOL_F);
      break;

    case BADDIE:
      robot_shields -= 10;
      if (robot_shields < 1)
	{
	  exit_nicely ();
	}
      return (SCM_BOOL_F);

    case FOOD:
      /* I want the net gain to be +10 */
      robot_energy += 15;
      break;

    case PRIZE:
      robot_score++;
      break;
    }

  /* only successful grabs get here */

  map[y_to][x_to] = SPACE;
  hook_add_thing (x_to, y_to, SPACE);
  return (SCM_BOOL_T);
}

SCM robot_zap (void)
{
  int x_to, y_to;
  int dx, dy;

  /* determine changes to x,y */

  switch (robot_dir)
    {
    case 0:			/* N */
      dx = 0;
      dy = -1;
      break;
    case 1:			/* E */
      dx = 1;
      dy = 0;
      break;
    case 2:			/* S */
      dx = 0;
      dy = 1;
      break;
    case 3:			/* W */
      dx = -1;
      dy = 0;
      break;
    }

  /* Try to zap the thing */

  x_to = robot_x + dx;
  y_to = robot_y + dy;

  robot_energy -= 10;
  if (robot_energy < 1)
    {
      exit_nicely ();
    }
  hook_robot_zap (robot_x, robot_y, cdirs[robot_dir], x_to, y_to);

  /* Did we destroy it? */

  switch (map[y_to][x_to])
    {
    case SPACE:
    case WALL:
    case ROBOT:		/* what to w/ robots? */
      return (SCM_BOOL_F);
      break;

    case BADDIE:
    case FOOD:
    case PRIZE:
      hook_add_thing (x_to, y_to, SPACE);
      break;
    }

  /* only success gets here */

  map[y_to][x_to] = SPACE;
  hook_add_thing (x_to, y_to, SPACE);
  return (SCM_BOOL_T);
}

SCM robot_stop (void)
{
  /* Must be a SCM function, even though it returns no value */
  /* Stop the robot immediately */

  exit_nicely ();
  return (SCM_BOOL_T);		/* never gets here */
}

SCM robot_get_shields (void)
{
  /* Returns the robot shields */
  return (scm_from_long (robot_shields));
}

SCM robot_get_energy (void)
{
  /* Returns the robot energy */
  return (scm_from_long (robot_energy));
}

SCM robot_get_score (void)
{
  /* Returns the robot score */
  return (scm_from_long (robot_score));
}

int
what_thing (const char *th)
{
  /* what_thing - this function scans the list of possible things
     (strings) and returns a cthing.  Returns -1 if not found in the
     list. */

  /* My idea here is that by return -1 on error, this won't match
     anything int he list of cthings.  That way, the function that
     uses what_thing to determine the cthing doesn't have to care if
     the call failed or not.  This helps me keep the code simple,
     since now I don't have to add a branch for failure, but which
     also decrements energy. */

  int i;

  for (i = 0; i < 6; i++)
    {
      if (strcmp (th, things[i]) == 0)
	{
	  return (cthings[i]);
	}
    }				/* for */

  /* not found */

  return (-1);
}
