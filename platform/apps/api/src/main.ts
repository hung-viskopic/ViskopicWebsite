import 'reflect-metadata';
import { BadRequestException, Body, Controller, Get, Module, NotFoundException, Param, ParseUUIDPipe, Patch, Post, Query, UploadedFile, UseGuards, UseInterceptors } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { FileInterceptor } from '@nestjs/platform-express';
import { NestExpressApplication } from '@nestjs/platform-express';
import { S3Client, CreateBucketCommand, HeadBucketCommand, PutObjectCommand, DeleteObjectCommand } from '@aws-sdk/client-s3';
import { randomUUID, createHash } from 'node:crypto';
import { validateDocument } from './validation';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { activeStudent, audit, organisation, pool, required, transaction } from './store';
import { AccessGuard } from './security';
import { RecordsController } from './records';

const bucket = required('S3_BUCKET');
const s3 = new S3Client({ region: required('S3_REGION'), endpoint: required('S3_ENDPOINT'), forcePathStyle: true, credentials: { accessKeyId: required('S3_ACCESS_KEY'), secretAccessKey: required('S3_SECRET_KEY') } });

@Controller('api')
class HealthController {
  @Get('health') async health() {
    await pool.query('SELECT 1');
    await s3.send(new HeadBucketCommand({ Bucket: bucket }));
    return { status: 'ok' };
  }
}

function studentId(value: unknown): string | null {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new BadRequestException('Invalid student record ID.');
  return value;
}
@Controller('api')
@UseGuards(AccessGuard)
class SubmissionController {
  @Get('submissions') async list(@Query('studentId') student: string | undefined) {
    const id = student === 'unassigned' ? null : studentId(student);
    return (await pool.query('SELECT id,student_id,filename,byte_count,status,word_count,error,created_at FROM submissions WHERE organisation_id=$1 AND ($2::boolean OR student_id IS NOT DISTINCT FROM $3::uuid) ORDER BY created_at DESC', [organisation, student === undefined, id])).rows;
  }
  @Get('submissions/:id') async detail(@Param('id', new ParseUUIDPipe()) id: string) {
    const result = await pool.query('SELECT id,student_id,filename,sha256,byte_count,status,attempts,extracted_text,word_count,pipeline_version,error,created_at FROM submissions WHERE id=$1 AND organisation_id=$2', [id, organisation]);
    if (!result.rowCount) throw new NotFoundException();
    const events = await pool.query('SELECT event,created_at FROM processing_events WHERE submission_id=$1 ORDER BY id', [id]);
    await audit(pool, id, 'submission', 'Document viewed');
    return { ...result.rows[0], events: events.rows };
  }
  @Patch('submissions/:id') async assign(@Param('id', new ParseUUIDPipe()) id: string, @Body() body: Record<string, unknown>) {
    if (!body || !Object.hasOwn(body, 'studentId')) throw new BadRequestException('Student ID is required; use null for unassigned.');
    const student = studentId(body.studentId);
    return transaction(async db => {
      const destination = student ? await activeStudent(db, student) : null;
      const old = await db.query('SELECT student_id,filename FROM submissions WHERE id=$1 AND organisation_id=$2', [id, organisation]);
      if (!old.rowCount) throw new NotFoundException();
      await db.query('UPDATE submissions SET student_id=$3,updated_at=now() WHERE id=$1 AND organisation_id=$2', [id, organisation, student]);
      await audit(db, id, 'submission', destination ? `Assigned to student ${destination.reference}` : 'Moved to unassigned');
      if (old.rows[0].student_id) await audit(db, old.rows[0].student_id, 'student', `Document moved out: ${old.rows[0].filename}`);
      if (student) await audit(db, student, 'student', `Document assigned: ${old.rows[0].filename}`);
      return { id, student_id: student };
    });
  }
  @Post('submissions')
  @UseInterceptors(FileInterceptor('file', { limits: { fileSize: 10 * 1024 * 1024, files: 1, fields: 0 } }))
  async upload(@Query('studentId') studentValue: string | undefined, @UploadedFile() file?: Express.Multer.File) {
    const student = studentId(studentValue);
    if (!file) throw new BadRequestException('Choose a PDF, DOCX, or text file.');
    let format;
    try { format = validateDocument(file.originalname, file.buffer); } catch (error) { throw new BadRequestException((error as Error).message); }
    const id = randomUUID();
    const key = `${organisation}/${id}/original.${format.extension}`;
    await s3.send(new PutObjectCommand({ Bucket: bucket, Key: key, Body: file.buffer, ContentType: format.contentType }));
    try {
      await transaction(async client => {
      if (student) await activeStudent(client, student);
      await client.query('INSERT INTO submissions(id,organisation_id,filename,object_key,sha256,byte_count,student_id) VALUES($1,$2,$3,$4,$5,$6,$7)', [id, organisation, file.originalname.slice(0,255), key, createHash('sha256').update(file.buffer).digest('hex'), file.size, student]);
      await client.query('INSERT INTO processing_events(submission_id,event) VALUES($1,$2)', [id, 'Submission received']);
      await audit(client, id, 'submission', 'Document uploaded');
      if (student) await audit(client, student, 'student', `Document uploaded: ${file.originalname.slice(0,255)}`);
      });
    } catch (error) {
      await s3.send(new DeleteObjectCommand({ Bucket: bucket, Key: key })).catch(() => console.error('Orphan cleanup required', id));
      throw error;
    }
    return { id, status: 'queued' };
  }
}
@Module({ controllers: [HealthController, SubmissionController, RecordsController], providers: [AccessGuard] }) class AppModule {}
async function bootstrap() {
  await transaction(async db => {
    await db.query(await readFile(resolve(__dirname, '../../../database/001_initial.sql'), 'utf8'));
    await db.query(await readFile(resolve(__dirname, '../../../database/002_records.sql'), 'utf8'));
  });
  for (let attempt=0; ; attempt++) {
    try {
      try { await s3.send(new HeadBucketCommand({ Bucket: bucket })); }
      catch (error) {
        if ((error as { $metadata?: {httpStatusCode?: number} }).$metadata?.httpStatusCode !== 404) throw error;
        await s3.send(new CreateBucketCommand({ Bucket: bucket }));
      }
      break;
    } catch (error) { if (attempt >= 20) throw error; await new Promise(resolve => setTimeout(resolve, 1500)); }
  }
  const app = await NestFactory.create<NestExpressApplication>(AppModule);
  if (process.env.WEB_ROOT) app.useStaticAssets(process.env.WEB_ROOT);
  app.enableShutdownHooks();
  const port = Number(process.env.PORT || 4300);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be a valid TCP port');
  await app.listen(port, '0.0.0.0');
}
bootstrap().catch(error => { console.error(error); process.exit(1); });
