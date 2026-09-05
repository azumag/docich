/* Hooks for an X11 game display for GNU Robots */

/*
   Copyright (C) 1998 Jim Hall, jhall1@isd.net 
   Modified from curses.c by:
   Tom Whittock (shallow@dial.pipex.com), 1998 

   updated 12/22/99 jhall
   created the USLEEP_TIME constant, instead of 500000
*/

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

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/xpm.h>
#include <X11/keysym.h>

#include <string.h>
#include <stdio.h>				/* for printf */
#include <unistd.h>				/* for usleep */
#include "hooks.h"
#include "api.h"
#include "config.h"


/* SYMBOLIC CONSTANTS */

#define USLEEP_TIME 160			/* sleep time */
#define USLEEP_MULT 16				/* not used yet --jh */


/* Global variables */
  
Display *dpy;
Window x_win;
GC gc, buf_gc;
Font text;
Atom delete_win;
Visual *vis;
unsigned int depth;

#ifdef USE_MITSHM
  #include <sys/ipc.h>
  #include <sys/shm.h>
  #include <X11/extensions/XShm.h>

  XShmSegmentInfo shm_info;
  unsigned char use_mitshm = 1;
#endif

int ncols, nrows, robot_score, 
    robot_shields, robot_energy, robot_dir,
    win_width, win_height;

Pixmap win_buf;

XImage *win_bufi, *statusbar, *space, *food,
       *wall, *prize, *baddie, 
       *robotN, *robotE, *robotW, *robotS, *robot;

/* Prototype needed functions */
void exit_nicely (void);

/* Functions in this file */
void x11_update (const char *s, unsigned short snd);
void put_tile (XImage *image, int x, int y);
void put_winbuf (void);

/* hooks to add things to the displayed map */

/* note that hook_delete_thing(x,y) is the same as
   hook_add_thing(x,y,space) */

void
hook_add_thing (int x, int y, int thing)
{
  int w_x, w_y;
  
  w_x = x * TILE_SIZE;
  w_y = y * TILE_SIZE;
  
  switch (thing) {
    case SPACE :
      put_tile(space, w_x, w_y);
      break;
    case FOOD :
      put_tile(food, w_x, w_y);
      break;
    case PRIZE :
      put_tile(prize, w_x, w_y);
      break;
    case WALL :
      put_tile(wall, w_x, w_y);
      break;
    case BADDIE :
      put_tile(baddie, w_x, w_y);
      break;
    case ROBOT :
      put_tile(robot, w_x, w_y);
      break;
    default :
      put_tile(wall, w_x, w_y);
      break;
  }
  XFlush(dpy);
}

void
hook_add_robot (int x, int y, int cdir)
{
  const static int movement = TILE_SIZE / 16;
  static int ow_x = TILE_SIZE, ow_y = TILE_SIZE; /* This can't be right. */
  unsigned int w_x = x * TILE_SIZE, 
               w_y = y * TILE_SIZE,
               tw_x, tw_y;
  Bool ok,
       started_same = ((w_x == ow_x) && (w_y == ow_y));

  x11_update ("robot moves..", 5);
  tw_y = w_y;
  tw_x = w_x;
  switch (cdir) {
    case 'N':
      if (!started_same)
        tw_y = ow_y - movement;
      break;
    case 'S':
      if (!started_same)
        tw_y = ow_y + movement;
      break;
    case 'E':
      if (!started_same)
        tw_x = ow_x + movement;
      break;
    case 'W':
      if (!started_same)
        tw_x = ow_x - movement;
      break;
    default:
      printf("Weird unknown robot direction. I'm Confused.\n");
  }

  while (1) {
    put_tile(space, ow_x, ow_y);
    switch (cdir) {
      case 'N':
        put_tile(robotN, tw_x, tw_y);
        break;
      case 'E':
        put_tile(robotE, tw_x, tw_y);
        break;
      case 'W':
        put_tile(robotW, tw_x, tw_y);
        break;
      case 'S':
        put_tile(robotS, tw_x, tw_y);
        break;
    }
    ok = False;
    if (tw_x < w_x) {
      tw_x += movement;
      ok = True;
    } else if (tw_x > w_x) {
      tw_x -= movement;
      ok = True;
    }
    if (tw_y < w_y) {
      tw_y += movement;
      ok = True;
    } else if (tw_y > w_y) {
      tw_y -= movement;
      ok = True;
    }
    put_winbuf();
    XSync(dpy, False);
    if (!started_same)
      usleep(USLEEP_TIME/16);
    if (!ok)
      break;
  }
  if (!started_same) {
    usleep(USLEEP_TIME%16);
    ow_x = w_x;
    ow_y = w_y;
  } else {
    usleep(USLEEP_TIME);
  }
}

/* animation funcs */
/* Each should last for 500,000 u-secs. */

void
x11_smell_anim(int x, int y, int cdir)
{
	/* If we want to change the pic, do it here */
    usleep(USLEEP_TIME);
}

void
x11_zap_anim(int x, int y, int cdir, int x_to, int y_to)
{
	usleep(USLEEP_TIME);
}

void
x11_feel_anim(int x, int y, int cdir, int x_to, int y_to)
{
	usleep(USLEEP_TIME);
}

void
x11_look_anim (int x, int y, int cdir, int x_to, int y_to)
{
	usleep(USLEEP_TIME);
}

void
x11_grab_anim (int x, int y, int cdir, int x_to, int y_to)
{
	usleep(USLEEP_TIME);
}

/* hooks to animate the robot */

void
hook_robot_smell (int x, int y, int cdir)
{
  x11_update ("robot sniffs..", 0);
  x11_smell_anim(x, y, cdir);
}

void
hook_robot_zap (int x, int y, int cdir, int x_to, int y_to)
{
  x11_update ("robot fires little gun..", 1);
  x11_zap_anim(x, y, cdir, x_to, y_to);
}

void
hook_robot_feel (int x, int y, int cdir, int x_to, int y_to)
{
  x11_update ("robot feels for thing..", 2);
  x11_feel_anim(x, y, cdir, x_to, y_to);
}

void
hook_robot_look (int x, int y, int cdir, int x_to, int y_to)
{
  x11_update ("robot looks for thing..", 3);
  x11_look_anim(x, y, cdir, x_to, y_to);
}

void
hook_robot_grab (int x, int y, int cdir, int x_to, int y_to)
{
  x11_update ("robot grabs thing..", 4);
  x11_grab_anim(x, y, cdir, x_to, y_to);
}

#ifdef USE_MITSHM
int shm_error_handler(Display *d, XErrorEvent *e)
{
  use_mitshm = 0;
  return 0;
}
#endif

void
setup_winbuf (void)
{
  int major, minor;
  Bool shared;
  XVisualInfo *matches;
  XVisualInfo plate;
  int count;
  XGCValues values;
  
  vis = DefaultVisualOfScreen(DefaultScreenOfDisplay(dpy));
  plate.visualid = XVisualIDFromVisual(vis);
  matches = XGetVisualInfo(dpy, VisualIDMask, &plate, &count);
  depth = matches[0].depth;

#ifdef USE_MITSHM
  use_mitshm = 1;
  shm_info.shmid = shmget(IPC_PRIVATE, win_height * win_width * depth,
                          IPC_CREAT | 0777);
  if (shm_info.shmid < 0) {
    fprintf(stderr, "shmget failed, looks like I'll have to use XPutImage\n");
    use_mitshm = 0;
  } else {
    shm_info.shmaddr = (char *) shmat(shm_info.shmid, 0, 0);

    if (!shm_info.shmaddr) {
      fprintf(stderr, "shmat failed, looks like I'll have to use XPutImage\n");
      shmctl(shm_info.shmid, IPC_RMID, 0);
      use_mitshm = 0;
    } else {
      XErrorHandler error_handler = XSetErrorHandler(shm_error_handler);
      win_bufi = XShmCreateImage(dpy, vis, depth, ZPixmap, shm_info.shmaddr,
                                 &shm_info, win_width, win_height);
      shm_info.readOnly = False;
      XShmAttach(dpy, &shm_info);
      win_buf = XShmCreatePixmap(dpy, x_win, shm_info.shmaddr, &shm_info,
                                 win_width, win_height, depth);
      XSync(dpy, False);
      (void) XSetErrorHandler(error_handler);
      if (!use_mitshm) {
        fprintf(stderr,
                "XShmAttach failed, looks like I'll have to use XPutImage\n");
        XFreePixmap(dpy, win_buf);
        XDestroyImage(win_bufi);
        shmdt(shm_info.shmaddr);
        shmctl(shm_info.shmid, IPC_RMID, 0);
      }
    }
  }

  if (!use_mitshm) {
#endif /* USE_MITSHM */
    win_buf = XCreatePixmap(dpy, x_win, win_width, win_height, depth);
#ifdef USE_MITSHM
  } else {
    printf("Using MIT Shared Memory Pixmaps. Good.\n", major, minor);
  }
#endif

  values.font = text;
  values.foreground = WhitePixel(dpy, DefaultScreen(dpy));  

  buf_gc = XCreateGC(dpy, win_buf, GCFont|GCForeground, &values);
}

void
create_image(char **data, XImage **image)
{
  XpmCreateImageFromData(dpy, data, image, NULL, NULL);
}

void
put_tile(XImage *image, int x, int y)
{
  XPutImage(dpy, win_buf, gc, image, 0, 0, x, y, TILE_SIZE, TILE_SIZE);
}

void
update_status(const char *s)
{
  char *status = malloc(20);
  int x = 0;

  while (x < win_width) {
    XPutImage(dpy, win_buf, buf_gc, statusbar, 0, 0, x, nrows*TILE_SIZE, 96, 32);
    x = x + 96;
  }

  XDrawString(dpy, win_buf, gc, 3, nrows*TILE_SIZE+16, s, strlen(s));

  sprintf(status, "Robot Energy: %3d", robot_energy);
  XDrawString(dpy, win_buf, gc, 160, nrows*TILE_SIZE+12, status, 
              strlen(status));

  sprintf(status, "Robot Score: %3d", robot_score);
  XDrawString(dpy, win_buf, gc, 160, nrows*TILE_SIZE+25, status, 
              strlen(status));

  sprintf(status, "Robot Shields: %3d", robot_shields);
  XDrawString(dpy, win_buf, gc, 280, nrows*TILE_SIZE+12, status, 
              strlen(status));

  free(status);
}

void
put_winbuf(void)
{
#ifdef USE_MITSHM
  if (use_mitshm)
    XShmPutImage(dpy, x_win, gc, win_bufi, 0, 0, 0, 0, win_width, win_height, 
                 False);
  else
#endif
    XCopyArea(dpy, win_buf, x_win, gc, 0, 0, win_width, win_height, 0, 0);
  XSync(dpy, 0);
}

void
hook_init (void)
{
  /* Initialize X11 */
#include "xpm/statusbar.xpm"
#include "xpm/space.xpm"
#include "xpm/food.xpm"
#include "xpm/wall.xpm"
#include "xpm/prize.xpm"
#include "xpm/baddie.xpm"
#include "xpm/robot_north.xpm"
#include "xpm/robot_east.xpm"
#include "xpm/robot_south.xpm"
#include "xpm/robot_west.xpm"
#include "xpm/robot.xpm"

  Atom prots[6];
  XClassHint classhint;
  XWMHints   wmhints;
  XGCValues  values;
  int x;
      
  if ((dpy = XOpenDisplay("")) == NULL) {
    printf("Couldn't open the X Server Display!\n");
    exit(1); /* Exit nicely isn't needed yet, and causes segfault */
  }
  
  delete_win = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
  
  win_width = ncols*TILE_SIZE;
  win_height = nrows*TILE_SIZE+32;
  x_win = XCreateSimpleWindow(dpy, DefaultRootWindow(dpy), 0, 0, 
          win_width, win_height, 0, 0, 0);
  
  prots[0] = delete_win;
  XSetWMProtocols(dpy, x_win, prots, 1);
  
  XStoreName(dpy, x_win, "GNU Robots");
  
  classhint.res_name = "robots";
  classhint.res_class = "Robots";
  XSetClassHint(dpy, x_win, &classhint);
  
  /* XSetCommand() seems to segfault... */
  
  wmhints.input = True;
  wmhints.flags = InputHint;
  XSetWMHints(dpy, x_win, &wmhints);

  XSelectInput(dpy, x_win, KeyPressMask|KeyReleaseMask|StructureNotifyMask|
               FocusChangeMask);
  
  XMapWindow(dpy, x_win);

  text = XLoadFont(dpy, "-*-helvetica-medium-r-*-*-*-120-*-*-*-*-*-*");
  values.font = text;
  values.foreground = WhitePixel(dpy, DefaultScreen(dpy));  

  gc = XCreateGC(dpy, x_win, GCFont|GCForeground, &values);

  create_image(statusbar_xpm, &statusbar);
  create_image(space_xpm, &space);
  create_image(food_xpm, &food);
  create_image(wall_xpm, &wall);
  create_image(prize_xpm, &prize);
  create_image(baddie_xpm, &baddie);
  create_image(robot_north_xpm, &robotN);
  create_image(robot_east_xpm, &robotE);
  create_image(robot_west_xpm, &robotW);
  create_image(robot_south_xpm, &robotS);
  create_image(robot_xpm, &robot);

  setup_winbuf();

  update_status("Welcome to GNU Robots");
}

void
hook_close (void)
{
  /* End X11 mode */
#ifdef USE_MITSHM
  if (use_mitshm) {
    XShmDetach(dpy, &shm_info);
    if (shm_info.shmaddr) shmdt(shm_info.shmaddr);
    if (shm_info.shmid >= 0) shmctl(shm_info.shmid, IPC_RMID, 0);
  }
#endif
  XDestroyWindow(dpy, x_win);
  XUnloadFont(dpy, text);
}

void
x11_update (const char *s, unsigned short snd)
{
  int x;
  XEvent ev;
  
  update_status(s);

  while (XPending(dpy)) {
    XNextEvent(dpy, &ev);
    
    switch(ev.type) {
      case KeyPress:
      case KeyRelease:
        switch(XKeycodeToKeysym(dpy, ev.xkey.keycode, 0)) {
          case XK_Escape:    exit(0);          break;
        }
    }
  }

  put_winbuf();
}

         
