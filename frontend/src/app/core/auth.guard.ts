import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { inject } from '@angular/core';
import { map, of } from 'rxjs';
import { AuthService } from './auth.service';

function loginRedirect(router: Router, url: string): UrlTree {
  return router.createUrlTree(['/login'], { queryParams: { redirect: url } });
}

export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const current = auth.user();
  if (current) {
    if (current.must_change_password) {
      return router.createUrlTree(['/login'], { queryParams: { force: '1' } });
    }
    return true;
  }

  if (auth.loaded()) {
    return loginRedirect(router, state.url);
  }

  return auth.me().pipe(
    map(user => {
      if (!user) return loginRedirect(router, state.url);
      if (user.must_change_password) {
        return router.createUrlTree(['/login'], { queryParams: { force: '1' } });
      }
      return true;
    }),
  );
};
