/* Loads a game map into memory */

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

#include "api.h"
#include "loadmap.h"


/* Functions */

/* load_map - loads a map file into memory.  Returns the map. */

int **
load_map (const char *mapfile, int **map, int nrows, int ncols)
{
  FILE *stream;			/* the file to load */

  stream = fopen (mapfile, "r");

  if (stream != NULL)
    {
      fload_map (stream, map, nrows, ncols);
      fclose (stream);

      return (map);
    }

  /* else, no file to open */

  printf ("No such file, %s\n", mapfile);
  return (0);
}

/* fload_map - loads a map file into memory.  Returns the number of
   lines loaded into the map. */

int **
fload_map (FILE * stream, int **map, int nrows, int ncols)
{
  int ch;
  int i, j;

  /* Read the map file */

  i = 0;
  j = 0;

  while ((ch = fgetc (stream)) != EOF)
    {
      /* Add the ch to the map */

      switch (ch)
	{
	case SPACE:
	case WALL:
	case BADDIE:
	case PRIZE:
	case FOOD:
	  map[j][i] = ch;
	  break;

	case '\n':
	  /* ignore the newline */
	  break;

	default:
	  /* not a valid map char, but we'll add a space anyway */

	  map[j][i] = SPACE;
	  break;
	}			/* switch */

      /* Check for newline */

      if (ch == '\n')
	{
	  /* the line may have ended early.  pad with WALL */

	  while (i < ncols)
	    {
	      map[j][i++] = WALL;
	    }
	}			/* if newline */

      /* Check for limits on map */

      if (++i >= ncols)
	{
	  /* We have filled this line */

	  ++j;
	  i = 0;

	  /* Flush the buffer for this line */

	  while (ch != '\n')
	    {
	      ch = fgetc (stream);
	    }
	}			/* if i */

      if (j >= nrows)
	{
	  /* the map is filled */

	  return (map);
	}
    }				/* while fgetc */

  /* After the loop, we are done reading the map file.  Make sure this
     is bounded by WALL. */

  if (i > 0)
    {
      ++j;
    }

  for (i = 0; i < ncols; i++)
    {
      map[j][i] = WALL;
    }

  return (map);
}

int **
cleanup_map (int **map, int nrows, int ncols)
{
  int i, j;

  /* make sure there is a wall at top/bottom */

  for (i = 0; i < ncols; i++)
    {
      map[0][i] = WALL;
      map[nrows - 1][i] = WALL;
    }

  /* make sure there is a wall at left/right */

  for (j = 0; j < nrows; j++)
    {
      map[j][0] = WALL;
      map[j][ncols - 1] = WALL;
    }

  return (map);
}
