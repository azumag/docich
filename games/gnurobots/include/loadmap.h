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

#ifndef _LOADMAP_H
#define _LOADMAP_H

int **load_map (const char *mapfile, int **map, int nrows, int ncols);
int **fload_map (FILE *stream, int **map, int nrows, int ncols);
int **cleanup_map (int **map, int nrows, int ncols);

#endif /* _LOADMAP_H */
