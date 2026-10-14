/* Draws the map for the GNU Robots game */

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

#include "api.h"		/* GNU Robots API */
#include "hooks.h"		/* GNU Robots UI hooks */


/* Functions */

void
draw_map (int **map, int nrows, int ncols)
{
  int i, j;

  /* Draw the map for the GNU Robots game. */

  for (j = 0; j < nrows; j++)
    {
      for (i = 0; i < ncols; i++)
	{
	  /* Special cases */

	  switch (map[j][i])
	    {
	      /* Add something for the ROBOT?? */

	    case '\0':
	      hook_add_thing (i, j, '?');
	      break;

	    default:
	      hook_add_thing (i, j, map[j][i]);
	      break;
	    }			/* switch */
	}			/* for i */
    }				/* for j */

}				/* return */
