import { CanActivate, ExecutionContext, Injectable, UnauthorizedException } from '@nestjs/common';
import { validKey } from './validation';
import { required } from './store';
const accessKey = required('DEV_ACCESS_KEY');
@Injectable()
export class AccessGuard implements CanActivate {
  canActivate(context: ExecutionContext) {
    if (!validKey(context.switchToHttp().getRequest().headers['x-access-key'], accessKey)) throw new UnauthorizedException('Invalid development access key.');
    return true;
  }
}
