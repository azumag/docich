/* $Id: main.c,v 1.5 2000/05/28 21:00:40 jhall Exp $ */

/* GNU Robots game engine.  This is the main() program, using GNU
   Guile as my backend to handle the language. */

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
#include <unistd.h>		/* for getopt */

#include <getopt.h>		/* for GNU getopt_long */
#include <libguile.h>

void main_prog_thunk (void *closure, int argc, char *argv[]);		/* GNU Guile (3.x; gh_* ABI removed upstream) */

#include "api.h"		/* robot API, as Scheme functions */
#include "drawmap.h"		/* draws a game map */
#include "hooks.h"		/* user interface hooks */
#include "loadmap.h"		/* loads a game map */
#include "main.h"		/* for this source file */

#if 0				/* scm_random is included in guile 1.3 */
#include "random.h"		/* faked SCM primitives */
#endif

/* Symbolic constants */

#define MAP_XSIZE 40
#define MAP_YSIZE 20

#define DEFAULT_ENERGY 1000
#define DEFAULT_SHIELDS 100

#define VERSION   "GNU Robots, version 1.0"
#define COPYRIGHT "Copyright (C) 1998,1999,2000 Jim Hall <jhall1@isd.net>"


/* Globals (share with api.c) */

int **map;
int nrows = MAP_YSIZE;
int ncols = MAP_XSIZE;

int robot_x = 1;		/* the robot position, etc. */
int robot_y = 1;
int robot_dir = 0;

long robot_score = 0;		/* the robot score, etc. */
long robot_energy = DEFAULT_ENERGY;
long robot_shields = DEFAULT_SHIELDS;


/************************************************************************
 * main()                                                               *
 * The program starts here!                                             *
 ************************************************************************/

int
main (int argc, char *argv[])
{
  int opt;			/* the option read from getopt */

  int flag;			/* flag passed back from getopt - NOT
				   USED */

  char *main_argv[3] = { "GNU Robots",
    "test.scm",
    "mapfile.map"
  };

  struct option long_opts[] = {
    {"version", 0, NULL, 'V'},
    {"help", 0, NULL, 'h'},
    {"map-file", 1, NULL, 'f'},
    {"shields", 1, NULL, 's'},
    {"energy", 1, NULL, 'e'},
    {NULL, 0, NULL, 0}
  };

  /* Check command line */

  while ((opt = getopt_long (argc, argv, "Vhf:s:e:", long_opts, &flag)) !=
	 EOF)
    {
      switch (opt)
	{
	case 'V':

	  /* Display version, then quit */

	  fprintf (stderr, "%s\n", VERSION);
	  fprintf (stderr, "%s\n", COPYRIGHT);
	  exit (0);

	  break;

	case 'h':

	  /* Display help, then quit. */

	  usage (argv[0]);
	  exit (0);

	  break;

	case 'f':

	  /* Set map file */

	  main_argv[2] = optarg;	/* pointer assignment */

	  break;

	case 's':

	  /* Set shields */

	  robot_shields = (long) atol (optarg);

	  break;

	case 'e':

	  /* Set energy */

	  robot_energy = (long) atol (optarg);

	  break;

	default:

	  /* invalid option */

	  usage (argv[0]);
	  exit (1);

	  break;
	}			/* switch */
    }				/* while */

  /* Extra arg is the Scheme file */

  if (optind < argc)
    {
      /* Set Scheme file */

      main_argv[1] = argv[optind];	/* pointer assignment */
    }

  /* Check that files exist */

  if (!is_file (main_argv[1]))
    {
      fprintf (stderr, "%s: %s: Scheme file does not exist\n",
	       argv[0], main_argv[1]);

      exit (1);
    }

  if (!is_file (main_argv[2]))
    {
      fprintf (stderr, "%s: %s: game map file does not exist\n",
	       argv[0], main_argv[2]);

      exit (1);
    }

  /* Start Guile environment.  Does not exit */

  printf ("%s\n", VERSION);
  printf ("%s\n", COPYRIGHT);
  printf ("GNU Robots comes with ABSOLUTELY NO WARRANTY\n");
  printf ("This is free software, and you are welcome to redistribute it\n");
  printf ("under certain conditions; see the file `COPYING' for details.\n");
  printf ("Loading Guile ... Please wait\n");

  scm_boot_guile (2, main_argv, main_prog_thunk, NULL);

  exit (0);			/* never gets here, but keeps compiler
				   happy */
}

/************************************************************************
 * main_prog()                                                          *
 * the main program code that is executed after Guile starts up.  Pass  *
 * the Scheme program as argv[1] and the map file as argv[2].  The      *
 * program name is still argv[0].                                       *
 ************************************************************************/

/* Guile 3.x boot callback adapter (the old gh_enter ABI is gone). */
void
main_prog_thunk (void *closure, int argc, char *argv[])
{
  main_prog (argc, argv);
}

void
main_prog (int argc, char *argv[])
{
  int j;
  char *robot_program = argv[1];
  char *map_file = argv[2];

  /* gh_startup();              no longer needed in guile 1.2? */

  /* define some new builtins (hooks) so that they are available in
     Scheme. */

  scm_c_define_gsubr ("robot-turn", 1, 0, 0, (scm_t_subr) robot_turn);
  scm_c_define_gsubr ("robot-move", 1, 0, 0, (scm_t_subr) robot_move);
  scm_c_define_gsubr ("robot-smell", 1, 0, 0, (scm_t_subr) robot_smell);
  scm_c_define_gsubr ("robot-feel", 1, 0, 0, (scm_t_subr) robot_feel);
  scm_c_define_gsubr ("robot-look", 1, 0, 0, (scm_t_subr) robot_look);
  scm_c_define_gsubr ("robot-grab", 0, 0, 0, (scm_t_subr) robot_grab);
  scm_c_define_gsubr ("robot-zap", 0, 0, 0, (scm_t_subr) robot_zap);

  scm_c_define_gsubr ("robot-get-shields", 0, 0, 0, (scm_t_subr) robot_get_shields);
  scm_c_define_gsubr ("robot-get-energy", 0, 0, 0, (scm_t_subr) robot_get_energy);
  scm_c_define_gsubr ("robot-get-score", 0, 0, 0, (scm_t_subr) robot_get_score);

#if 0                           /* disabled code */
  /* scm_random is included in guile 1.3 */

  /* Scheme primitives not there in Guile 1.2 */

  gh_new_procedure1_0 ("random", scm_random);
  gh_new_procedure0_0 ("randomize", scm_randomize);
#endif                          /* disabled code */

  /* Redefine some existing Scheme primitives */

  scm_c_define_gsubr ("stop", 0, 0, 0, (scm_t_subr) robot_stop);
  scm_c_define_gsubr ("quit", 0, 0, 0, (scm_t_subr) robot_stop);

  /* Create the map array, and load the map */

  map = malloc (nrows * sizeof (int **));

  for (j = 0; j < nrows; j++)
    {
      map[j] = malloc (ncols * sizeof (int));
    }

  printf ("Map file: %s\n", map_file);

  load_map (map_file, map, nrows, ncols);
  cleanup_map (map, nrows, ncols);

  /* ensure the robot is placed properly */

  robot_x = 1;
  robot_y = 1;
  robot_dir = 1;
  map[robot_y][robot_x] = ROBOT;

  /* draw the map */

  printf ("Robot program: %s\n", robot_program);
  hook_init ();
  draw_map (map, nrows, ncols);

  /* execute a Scheme file */

  scm_c_primitive_load (robot_program);

  /* done */

  exit_nicely ();
}

/************************************************************************
 * exit_nicely()                                                        *
 * A function that allows the program to exit nicely, after freeing all *
 * memory pointers, etc.                                                *
 ************************************************************************/

void
exit_nicely (void)
{
  int j;

  /* Stop the UI */

  hook_close ();

  /* Free memory */

  for (j = 0; j < nrows; j++)
    {
      free (map[j]);
    }
  free (map);

  /* Show statistics */

  printf ("\n-----------------------STATISTICS-----------------------\n");
  printf ("Shields: %ld\n", (robot_shields < 0 ? 0 : robot_shields));
  printf ("Energy: %ld\n", (robot_energy < 0 ? 0 : robot_energy));
  printf ("Score: %ld\n", robot_score);

  /* Show results, if any */

  if (robot_shields < 1)
    {
      printf ("** Robot took too much damage, and died.\n");
    }

  else if (robot_energy < 1)
    {
      printf ("** Robot ran out of energy.\n");
    }

  /* Quit program */

  exit (0);
}

/************************************************************************
 * usage()                                                              *
 * A function that prints the usage of GNU Robots to the user.  Assume  *
 * text mode for this function.  We have not initialized X Windows or   *
 * curses yet.                                                          *
 ************************************************************************/

void
usage (const char *argv0)
{
  printf ("%s\n", VERSION);
  printf ("%s\n", COPYRIGHT);
  printf
    ("Game/diversion where you construct a program for a little robot\n");
  printf ("then set him loose and watch him explore a world on his own.\n\n");

  printf ("Usage: %s [OPTION]... [FILE]\n", argv0);
  printf ("  -f, --map-file=FILE    Load map file\n");
  printf ("  -s, --shields=N        Set initial shields to N\n");
  printf ("  -e, --energy=N         Set initial energy to N\n");
  printf ("  -V, --version          Output version information and exit\n");
  printf ("  -h, --help             Display this help and exit\n");
}

/************************************************************************
 * is_file()                                                            *
 * Checks if a file is a readable file.  We will use this function as   *
 * part of a sanity check, before we get anywhere near having to open   *
 * files.  This will save on error checking later on, when we may have  *
 * already initialized another environment (Curses, X Windows, ...)     *
 ************************************************************************/

int
is_file (const char *filename)
{
  FILE *stream;

  stream = fopen (filename, "r");
  if (stream == NULL)
    {
      /* Failed */

      return (0);
    }

  /* Success */

  fclose (stream);
  return (1);
}
