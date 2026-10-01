import { BadRequestException, Body, ConflictException, Controller, Delete, Get, NotFoundException, Param, ParseUUIDPipe, Patch, Post, UseGuards } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { audit, label, organisation, pool, transaction } from './store';
import { AccessGuard } from './security';

@Controller('api')
@UseGuards(AccessGuard)
export class RecordsController {
  @Get('courses') async courses() {
    return (await pool.query('SELECT c.*, (SELECT count(*)::int FROM enrolments e WHERE e.course_id=c.id AND e.organisation_id=c.organisation_id) AS student_count FROM courses c WHERE organisation_id=$1 ORDER BY lower(name),id', [organisation])).rows;
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
      if ((await db.query('SELECT student_id FROM enrolments WHERE course_id=$1 AND organisation_id=$2 LIMIT 1', [id, organisation])).rowCount) throw new ConflictException('Only empty courses can be deleted. Archive this course instead.');
      if (!(await db.query('DELETE FROM courses WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
      await audit(db, id, 'course', 'Course deleted');
      return { deleted: true };
    });
  }
  @Get('courses/:id/students') async students(@Param('id', new ParseUUIDPipe()) id: string) {
    if (!(await pool.query('SELECT id FROM courses WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
    return (await pool.query('SELECT s.*, e.archived AS enrolment_archived, (SELECT count(*)::int FROM submissions d WHERE d.student_id=s.id AND d.course_id=e.course_id AND d.organisation_id=s.organisation_id) AS document_count FROM students s JOIN enrolments e ON e.student_id=s.id AND e.organisation_id=s.organisation_id WHERE e.course_id=$1 AND s.organisation_id=$2 ORDER BY lower(reference),s.id', [id, organisation])).rows;
  }
  @Post('courses/:id/students') async createStudent(@Param('id', new ParseUUIDPipe()) course: string, @Body() body: Record<string, unknown>) {
    const reference = label(body?.reference, 'Student reference');
    const name = label(body?.name ?? '', 'Display name', true);
    return transaction(async db => {
      const parent = await db.query('SELECT archived FROM courses WHERE id=$1 AND organisation_id=$2', [course, organisation]);
      if (!parent.rowCount) throw new NotFoundException();
      if (parent.rows[0].archived) throw new ConflictException('Restore the course before adding students.');
      const id = randomUUID();
      const row = await db.query('INSERT INTO students(id,organisation_id,reference,name) VALUES($1,$2,$3,$4) RETURNING *', [id, organisation, reference, name]);
      await db.query('INSERT INTO enrolments(student_id,course_id,organisation_id) VALUES($1,$2,$3)', [id, course, organisation]);
      await audit(db, id, 'student', 'Student record created');
      return row.rows[0];
    });
  }
  @Get('students') async allStudents() {
    return (await pool.query('SELECT s.*, (SELECT count(*)::int FROM submissions d WHERE d.student_id=s.id AND d.organisation_id=s.organisation_id) AS document_count, (SELECT count(*)::int FROM enrolments e WHERE e.student_id=s.id AND e.organisation_id=s.organisation_id) AS course_count FROM students s WHERE organisation_id=$1 ORDER BY lower(reference),id', [organisation])).rows;
  }
  @Post('students') async createProfile(@Body() body: Record<string, unknown>) {
    const reference = label(body?.reference, 'Student reference');
    const name = label(body?.name ?? '', 'Display name', true);
    return transaction(async db => {
      const id = randomUUID();
      const row = await db.query('INSERT INTO students(id,organisation_id,reference,name) VALUES($1,$2,$3,$4) RETURNING *', [id, organisation, reference, name]);
      await audit(db, id, 'student', 'Student record created');
      return row.rows[0];
    });
  }
  @Get('students/:id/courses') async studentCourses(@Param('id', new ParseUUIDPipe()) id: string) {
    if (!(await pool.query('SELECT id FROM students WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
    return (await pool.query('SELECT c.*,e.archived AS enrolment_archived FROM enrolments e JOIN courses c ON c.id=e.course_id AND c.organisation_id=e.organisation_id WHERE e.student_id=$1 AND e.organisation_id=$2 ORDER BY lower(c.name),c.id', [id, organisation])).rows;
  }
  @Post('courses/:id/enrolments') async enrol(@Param('id', new ParseUUIDPipe()) course: string, @Body() body: Record<string, unknown>) {
    if (typeof body?.studentId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.studentId)) throw new BadRequestException('Valid student ID is required.');
    return transaction(async db => {
      const parent = await db.query('SELECT archived FROM courses WHERE id=$1 AND organisation_id=$2', [course, organisation]);
      const student = await db.query('SELECT archived FROM students WHERE id=$1 AND organisation_id=$2', [body.studentId, organisation]);
      if (!parent.rowCount || !student.rowCount) throw new NotFoundException();
      if (parent.rows[0].archived || student.rows[0].archived) throw new ConflictException('Restore the student and course before enrolling.');
      const result = await db.query('INSERT INTO enrolments(student_id,course_id,organisation_id) VALUES($1,$2,$3) RETURNING *', [body.studentId, course, organisation]);
      await audit(db, body.studentId as string, 'student', `Enrolled in course ${course}`);
      return result.rows[0];
    });
  }
  @Patch('courses/:course/enrolments/:student') async archiveEnrolment(@Param('course', new ParseUUIDPipe()) course: string, @Param('student', new ParseUUIDPipe()) student: string, @Body() body: Record<string, unknown>) {
    if (typeof body?.archived !== 'boolean') throw new BadRequestException('Archived must be a boolean.');
    return transaction(async db => {
      const result = await db.query('UPDATE enrolments SET archived=$4 WHERE course_id=$1 AND student_id=$2 AND organisation_id=$3 RETURNING *', [course, student, organisation, body.archived]);
      if (!result.rowCount) throw new NotFoundException();
      await audit(db, student, 'student', `${body.archived ? 'Archived' : 'Restored'} enrolment in course ${course}`);
      return result.rows[0];
    });
  }
  @Delete('courses/:course/enrolments/:student') async removeEnrolment(@Param('course', new ParseUUIDPipe()) course: string, @Param('student', new ParseUUIDPipe()) student: string) {
    return transaction(async db => {
      if ((await db.query('SELECT id FROM submissions WHERE course_id=$1 AND student_id=$2 AND organisation_id=$3 LIMIT 1', [course, student, organisation])).rowCount) throw new ConflictException('This enrolment has documents. Archive it instead.');
      if (!(await db.query('DELETE FROM enrolments WHERE course_id=$1 AND student_id=$2 AND organisation_id=$3', [course, student, organisation])).rowCount) throw new NotFoundException();
      await audit(db, student, 'student', `Removed enrolment in course ${course}`);
      return { deleted: true };
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
      await db.query('DELETE FROM enrolments WHERE student_id=$1 AND organisation_id=$2', [id, organisation]);
      if (!(await db.query('DELETE FROM students WHERE id=$1 AND organisation_id=$2', [id, organisation])).rowCount) throw new NotFoundException();
      await audit(db, id, 'student', 'Student record deleted');
      return { deleted: true };
    });
  }
  @Get('activity/:id') async activity(@Param('id', new ParseUUIDPipe()) id: string) {
    return (await pool.query('SELECT id,action,actor,created_at FROM audit_events WHERE entity_id=$1 AND organisation_id=$2 ORDER BY id DESC LIMIT 100', [id, organisation])).rows;
  }
}
