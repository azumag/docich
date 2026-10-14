#ifndef _HOOKS_H
#define _HOOKS_H

/* Hooks for GNU Robots. */


/* hooks to add things to the displayed map */

/* note that hook_delete_thing(x,y) is the same as
   hook_add_thing(x,y,space) */

void hook_add_thing (int x, int y, int thing);
void hook_add_robot (int x, int y, int cdir);

/* hooks to animate the robot */

void hook_robot_smell (int x, int y, int cdir);
void hook_robot_zap (int x, int y, int cdir, int x_to, int y_to);
void hook_robot_feel (int x, int y, int cdir, int x_to, int y_to);
void hook_robot_look (int x, int y, int cdir, int x_to, int y_to);
void hook_robot_grab (int x, int y, int cdir, int x_to, int y_to);

/* hooks to startup and to stop */

void hook_init (void);
void hook_close (void);

#endif /* _HOOKS_H */
