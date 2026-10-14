#include <stdio.h>

/* is there a standard C function to do this? */

int
sign (int n)
{
  if (n < 0)
    {
      return (-1);
    }
  else if (n > 0)
    {
      return (+1);
    }

  return (0);
}
