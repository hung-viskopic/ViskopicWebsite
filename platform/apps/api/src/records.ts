import { BadRequestException, Body, ConflictException, Controller, Delete, Get, NotFoundException, Param, ParseUUIDPipe, Patch, Post, UseGuards } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { audit, label, organisation, pool, transaction } from './store';
import { AccessGuard } from './security';

@Controller('api')
@UseGuards(AccessGuard)
export class RecordsController {
  @Get('courses') async courses() {
    return (await pool.query('SELECT c.*, (SELECT count(*)::int FROM students s WHERE s.course_id=c.id) AS student_count FROM courses c WHERE organisation_id=$1 ORDER BY lower(name),id', [organisation])).rows;
  }
  @Post('courses') async createCourse(@Body() body: Record<string, unknown>) {
    const name = label(body?.name, 'Course name');
    return transaction(async db => {
      const id = randomUUID();
      const row = await db.query('INSERT INTO courses(id,organisation_id,name) VALUES($1,$2,$3) RETURNING *', [id, organisation, name]);
      await audit(db, id, 'course', 'Course created');
      return row.rows[0];
    });
  }
  @Patch('courses/:id') async updateCourse(@Param('id', new ParseUUIDPipe()) id: string, @Body() body: Record<string, unknown>) {
    const name = body?.name === undefined ? null : label(body.name, 'Course name');
    if (body?.archived !== undefined && typeof body.archived !== 'boolean') throw new BadRequestException('Archived must be a boolean.');
    return transaction(async db => {
      const row = await db.query('UPDATE courses SET name=COALESCE($3,name),archived=COALESCE($4,archived) WHERE id=$1 AND organisation_id=$2 RETURNING *', [id, organisation, name, body?.archived ?? null]);
      if (!row.rowCount) throw new NotFoundException();
      await audit(db, id, 'course', body?.archived === true ? 'Course archived' : body?.archived === false ? 'Course restored' : 'Course renamed');
      return row.rows[0];
    });
  }
  @Delete('courses/:id') async deleteCourse(@Param('id', new ParseUUIDPipe()) id: string) {
    return transaction(async db => {
      if ((await db.query('SELECT id FROM students WHERE course_id=$1 AND organisation_id=$2 LIMIT 1', [id, organisation])).rowCount) throw new ConflictException('Only empty courses can be deleted. Archive this course instead.');
      if (!(await db.query('DELETE FROM courses WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
      await audit(db, id, 'course', 'Course deleted');
      return { deleted: true };
    });
  }
  @Get('courses/:id/students') async students(@Param('id', new ParseUUIDPipe()) id: string) {
    if (!(await pool.query('SELECT id FROM courses WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
    return (await pool.query('SELECT s.*, (SELECT count(*)::int FROM submissions d WHERE d.student_id=s.id) AS document_count FROM students s WHERE s.course_id=$1 AND s.organisation_id=$2 ORDER BY lower(reference),id', [id, organisation])).rows;
  }
  @Post('courses/:id/students') async createStudent(@Param('id', new ParseUUIDPipe()) course: string, @Body() body: Record<string, unknown>) {
    const reference = label(body?.reference, 'Student reference');
    const name = label(body?.name ?? '', 'Display name', true);
    return transaction(async db => {
      const parent = await db.query('SELECT archived FROM courses WHERE id=$1 AND organisation_id=$2', [course, organisation]);
      if (!parent.rowCount) throw new NotFoundException();
      if (parent.rows[0].archived) throw new ConflictException('Restore the course before adding students.');
      const id = randomUUID();
      const row = await db.query('INSERT INTO students(id,organisation_id,course_id,reference,name) VALUES($1,$2,$3,$4,$5) RETURNING *', [id, organisation, course, reference, name]);
      await audit(db, id, 'student', 'Student record created');
      return row.rows[0];
    });
  }
  @Get('students/:id') async student(@Param('id', new ParseUUIDPipe()) id: string) {
    return transaction(async db => {
      const row = await db.query('SELECT * FROM students WHERE id=$1 AND organisation_id=$2', [id, organisation]);
      if (!row.rowCount) throw new NotFoundException();
      await audit(db, id, 'student', 'Student record viewed');
      return row.rows[0];
    });
  }
  @Patch('students/:id') async updateStudent(@Param('id', new ParseUUIDPipe()) id: string, @Body() body: Record<string, unknown>) {
    const reference = body?.reference === undefined ? null : label(body.reference, 'Student reference');
    const name = body?.name === undefined ? null : label(body.name, 'Display name', true);
    if (body?.archived !== undefined && typeof body.archived !== 'boolean') throw new BadRequestException('Archived must be a boolean.');
    return transaction(async db => {
      const row = await db.query('UPDATE students SET reference=COALESCE($3,reference),name=COALESCE($4,name),archived=COALESCE($5,archived) WHERE id=$1 AND organisation_id=$2 RETURNING *', [id, organisation, reference, name, body?.archived ?? null]);
      if (!row.rowCount) throw new NotFoundException();
      await audit(db, id, 'student', body?.archived === true ? 'Student archived' : body?.archived === false ? 'Student restored' : 'Student details updated');
      return row.rows[0];
    });
  }
  @Delete('students/:id') async deleteStudent(@Param('id', new ParseUUIDPipe()) id: string) {
    return transaction(async db => {
      if ((await db.query('SELECT id FROM submissions WHERE student_id=$1 AND organisation_id=$2 LIMIT 1', [id, organisation])).rowCount) throw new ConflictException('Only empty student records can be deleted. Move their documents or archive the record.');
      if (!(await db.query('DELETE FROM students WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
      await audit(db, id, 'student', 'Student record deleted');
      return { deleted: true };
    });
  }
  @Get('activity/:id') async activity(@Param('id', new ParseUUIDPipe()) id: string) {
    return (await pool.query('SELECT id,action,actor,created_at FROM audit_events WHERE entity_id=$1 AND organisation_id=$2 ORDER BY id DESC LIMIT 100', [id, organisation])).rows;
  }
}
